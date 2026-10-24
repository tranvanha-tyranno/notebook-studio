"""Local notebook/GPU launcher: python studio.py. Binds only to 127.0.0.1."""
import atexit
from collections import deque
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

from studio_project import package_list, project_key, stage_project, validate_notebook
from studio_pricing import SNAPSHOT, hourly, refresh
from studio_accounts import Accounts
from studio_billing import Billing

ROOT = Path(__file__).resolve().parent
STORE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NotebookStudio"
PYTHON = ROOT / ".venv/Scripts/python.exe" if os.name == "nt" else ROOT / ".venv/bin/python"
GPUS = tuple(SNAPSHOT["gpu"])


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def list_folder(value):
    folder = Path(value or ROOT).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Đường dẫn phải là thư mục")
    items = []
    for child in folder.iterdir():
        if child.name.startswith(".") or child.name in {"node_modules", "__pycache__", "venv"}:
            continue
        try:
            if child.is_dir() or child.suffix.lower() == ".ipynb":
                items.append({"name": child.name, "path": str(child), "folder": child.is_dir()})
        except OSError:
            continue
    return {"path": str(folder), "parent": str(folder.parent),
            "items": sorted(items, key=lambda item: (not item["folder"], item["name"].casefold()))[:500]}


class Manager:
    def __init__(self, store=STORE, *, accounts=None):
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.accounts = accounts or Accounts(self.store, PYTHON)
        self.billing = Billing(self.accounts)
        self.sessions = {}
        self.prices = copy.deepcopy(SNAPSHOT)
        self.projects = []
        self.transfers = {}
        try:
            for transfer in json.loads((self.store / "transfers.json").read_text(encoding="utf-8")):
                if transfer["state"] == "saving":
                    transfer.update(state="error", message="Launcher đã đóng khi lưu file; chọn lưu lại vào thư mục mới.")
                self.transfers[transfer["id"]] = transfer
        except (OSError, ValueError, KeyError):
            pass
        try:
            self.projects = json.loads((self.store / "projects.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

        # Restored sessions are not assumed stopped: the user can explicitly stop their app.
        try:
            saved = json.loads((self.store / "sessions.json").read_text(encoding="utf-8"))
            for value in saved:
                value.update(process=None, logs=deque(value.get("logs", []), maxlen=300), stopping=False)
                if value["state"] not in ("stopped", "error"):
                    value["state"] = "unknown"
                self.sessions[value["id"]] = value
        except (OSError, ValueError):
            pass

        # Bind legacy sessions once; later changes to the default cannot reroute them.
        migrated = False
        for session in self.sessions.values():
            if not session.get("account_id"):
                try:
                    profile = session.get("profile")
                    if not profile and session.get("dashboard"):
                        match = re.search(r"https://modal\.com/apps/([^/]+)/", session["dashboard"])
                        profile = match.group(1) if match else None
                    account = self.accounts.resolve(profile=profile)
                except ValueError:
                    continue
                session.update(account_id=account["id"], account_label=account["label"], profile=account["profile"],
                               account_scope=account.get("workspace") or account["id"])
                migrated = True
        if migrated:
            self.persist()

    def remember(self, folder, notebook):
        key = project_key(folder)
        value = {"key": key, "name": Path(folder).name, "folder": str(folder), "notebook": str(notebook)}
        self.projects = [value, *[project for project in self.projects if project["key"] != key]][:50]
        atomic_json(self.store / "projects.json", self.projects)
        return value

    def public(self, session):
        result = {key: value for key, value in session.items() if key not in ("process", "logs", "stopping")}
        result["logs"] = list(session["logs"])
        result["elapsed"] = max(0, (session.get("ended") or time.time()) - session["started"])
        segments = session.get("segments", [])
        result["estimated_cost"] = sum(
            max(0, (segment.get("end") or session.get("ended") or time.time()) - segment["start"])
            * segment["hourly"] / 3600 for segment in segments)
        return result

    def persist(self):
        atomic_json(self.store / "sessions.json", [self.public(value) for value in self.sessions.values()])

    def status(self):
        with self.lock:
            accounts = self.accounts.status()
            for account in accounts["items"]:
                account["billing"] = self.billing.public(account)
            return {"projects": self.projects, "sessions": [self.public(s) for s in reversed(list(self.sessions.values()))],
                    "prices": self.prices, "project_root": str(ROOT), "python_ready": PYTHON.is_file(),
                    "transfers": list(self.transfers.values()), "local_save_available": True,
                    "accounts": accounts}

    def session_account(self, session):
        return self.accounts.resolve(session.get("account_id"), profile=session.get("profile"))

    def logout_account(self, account_id):
        with self.lock:
            if any(s.get("account_id") == account_id and s["state"] not in ("stopped", "error") for s in self.sessions.values()):
                raise ValueError("Dừng hoặc kiểm tra các phiên của tài khoản này trước khi đăng xuất")
            if any(t["state"] == "saving" and self.sessions[t["session_id"]].get("account_id") == account_id
                   for t in self.transfers.values()):
                raise ValueError("Chờ lưu file xong trước khi đăng xuất")
            # An active alias may share the same credential file/profile.
            record = self.accounts.records[account_id]
            if record["source"] == "Studio" and any(
                self.accounts.records.get(s.get("account_id"), {}).get("config") == record["config"]
                and s["state"] not in ("stopped", "error") for s in self.sessions.values()
            ):
                raise ValueError("Dừng các phiên đang dùng thông tin đăng nhập này trước")
            return self.accounts.logout(account_id)

    def _transfer_options(self, session_id, action, **values):
        with self.lock:
            session = self.sessions[session_id]
            key = session["project_key"]
            if not re.fullmatch(r"[a-f0-9]{16}", key):
                raise ValueError("Mã dự án không hợp lệ")
            account = self.session_account(session)
            return {"action": action, "project_key": key, "profile": account["profile"], "account_id": account["id"], **values}

    def _transfer_process(self, options):
        account = self.accounts.resolve(options["account_id"])
        return subprocess.Popen([str(PYTHON), "-X", "utf8", str(ROOT / "studio_transfer.py")],
            cwd=ROOT, env=self.accounts.environment(account), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def remote_files(self, session_id, path=""):
        from studio_transfer import relative_path
        relative_path(path)
        options = self._transfer_options(session_id, "list", path=path)
        process = self._transfer_process(options)
        try:
            stdout, stderr = process.communicate(json.dumps(options), timeout=45)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
            raise ValueError("Modal chưa trả danh sách file. Thử lại sau.")
        for line in reversed(stdout.splitlines()):
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if "error" in result:
                raise ValueError(result["error"])
            if "result" in result and process.returncode == 0:
                return {"path": path, **result["result"]}
        raise ValueError("Không đọc được Volume. Kiểm tra tài khoản Modal của dự án.")

    def save_local(self, session_id, paths):
        from studio_transfer import relative_path
        if not isinstance(paths, list) or not paths or len(paths) > 500:
            raise ValueError("Chọn từ 1 đến 500 mục")
        for path in paths:
            relative_path(path)
        with self.lock:
            if any(t["session_id"] == session_id and t["state"] == "saving" for t in self.transfers.values()):
                raise ValueError("Dự án đang lưu file; chờ hoàn tất")
            session = self.sessions[session_id]
            transfer_id = uuid.uuid4().hex[:12]
            folder = Path(session["folder"]) / "modal-downloads" / (
                datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + transfer_id[:6])
            options = self._transfer_options(session_id, "save", paths=paths, destination=str(folder))
            transfer = {"id": transfer_id, "session_id": session_id, "state": "saving",
                        "destination": str(folder), "files": 0, "bytes": 0, "message": "Đang lấy file từ Modal Volume…"}
            self.transfers[transfer_id] = transfer
            atomic_json(self.store / "transfers.json", list(self.transfers.values()))
        threading.Thread(target=self._save_worker, args=(transfer_id, options), daemon=True).start()
        return dict(transfer)

    def _save_worker(self, transfer_id, options):
        transfer = self.transfers[transfer_id]
        try:
            process = self._transfer_process(options)
            process.stdin.write(json.dumps(options)); process.stdin.close()
            # SDK progress is JSON on stdout; drain stderr so a failure cannot block the process.
            threading.Thread(target=lambda: process.stderr.read(), daemon=True).start()
            result = None
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    if "error" in event:
                        result = event
                    elif "result" in event:
                        result = event
                    elif "progress" in event:
                        transfer.update(files=event["progress"], bytes=transfer["bytes"] + event["bytes"],
                                        message=f"Đã lưu {event['file']}")
            code = process.wait()
            if code or not result or "error" in result:
                raise ValueError((result or {}).get("error", "Không lưu được file; kiểm tra kết nối Modal"))
            with self.lock:
                transfer.update(result["result"])
                transfer.update(state="complete", message="Đã lưu trực tiếp vào máy. Không cần tải bằng Chrome.")
        except Exception as error:
            with self.lock:
                transfer.update(state="error", message=str(error))
        finally:
            with self.lock:
                atomic_json(self.store / "transfers.json", list(self.transfers.values()))

    def open_saved_folder(self, transfer_id):
        transfer = self.transfers[transfer_id]
        if transfer["state"] != "complete":
            raise ValueError("Chờ lưu file xong trước khi mở thư mục")
        if os.name == "nt":
            os.startfile(transfer["destination"])
        return {"path": transfer["destination"]}

    def start(self, options):
        folder, notebook = validate_notebook(options.get("folder", ""), options.get("notebook", ""))
        gpu = options.get("gpu", "T4")
        minutes, cpu, ram, epochs = (int(options.get(key, default)) for key, default in
                                     (("minutes", 60), ("cpu", 4), ("ram", 16), ("epochs", 0)))
        if gpu not in GPUS or not 1 <= minutes <= 240 or cpu not in (2, 4, 8) or ram not in (8, 16, 32, 64) or not 0 <= epochs <= 100000:
            raise ValueError("Cấu hình GPU/CPU/RAM/thời gian không hợp lệ")
        if options.get("split", "public_test") not in ("public_test", "private_test"):
            raise ValueError("Split không hợp lệ")
        packages = " ".join(package_list(options.get("packages", "")))
        data_volume = options.get("data_volume", "").strip()
        account = self.accounts.resolve(options.get("account_id"), profile=options.get("profile", "").strip())
        profile = account["profile"]
        if data_volume and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", data_volume):
            raise ValueError("Tên Volume không hợp lệ")
        if not PYTHON.is_file():
            raise ValueError("Chạy .\\modal.ps1 setup một lần để cài Modal client")
        with self.lock:
            key = project_key(folder)
            scope = account.get("workspace") or account["id"]
            if any(s["project_key"] == key and (
                s.get("account_id") == account["id"] or s.get("account_scope") == scope
                or (account.get("workspace") and self.accounts.records.get(s.get("account_id"), {}).get("workspace") == account["workspace"]))
                   and s["state"] not in ("stopped", "error") for s in self.sessions.values()):
                raise ValueError("Dự án đang mở trong tài khoản/workspace này. Chọn tài khoản khác hoặc dừng phiên trước")
            session_id = uuid.uuid4().hex[:12]
            staged = self.store / "staging" / session_id
            source = stage_project(folder, notebook, staged, options.get("include_code", True))
            environment = self.accounts.environment(account)
            # Do not inherit a previous launcher's project/config overrides.
            for name in list(environment):
                if name.startswith("CA2_"):
                    environment.pop(name)
            environment.update({
                "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "CA2_GPU": gpu,
                "CA2_CPU": str(cpu), "CA2_RAM_GIB": str(ram), "CA2_STAGED_PROJECT": str(staged),
                "CA2_ACCOUNT_LABEL": account["label"], "CA2_ACCOUNT_WORKSPACE": account.get("workspace") or profile,
                "CA2_PROJECT_KEY": key, "CA2_PROJECT_NAME": folder.name,
                "CA2_NOTEBOOK_RELATIVE": source["notebook"], "CA2_STUDIO_VOLUME": "notebook-studio",
                "CA2_EXTRA_PACKAGES": packages, "CA2_REFRESH_LOCAL": "1" if options.get("refresh_local") else "0",
                "CA2_PREDICT_ONLY": "1" if options.get("predict_only") else "0",
                "CA2_RESUME": "0" if options.get("fresh_train") else "1",
            })
            if data_volume:
                environment["CA2_DATA_VOLUME"] = data_volume
            # Modal options must precede the function reference. Options after
            # modal_lab.py belong to its local entrypoint, which has no --profile.
            command = [str(PYTHON), "-X", "utf8", "-m", "modal", "run", "--profile", profile,
                       "modal_lab.py", "--no-browser",
                       "--minutes", str(minutes), "--epochs", str(epochs), "--split", options.get("split", "public_test")]
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            session = {"id": session_id, "project_key": key, "name": folder.name, "notebook": str(notebook),
                       "folder": str(folder), "gpu": gpu, "cpu": cpu, "ram": ram, "minutes": minutes,
                       "profile": profile, "started": time.time(), "ended": None, "state": "starting",
                       "account_id": account["id"], "account_label": account["label"], "account_scope": scope,
                       "url": None, "app_id": None, "dashboard": None, "process": process,
                       "logs": deque(maxlen=300), "segments": [], "stopping": False,
                       "uploaded_files": source["files"]}
            self.sessions[session_id] = session
            self.remember(folder, notebook)
            self.persist()
            threading.Thread(target=self._read, args=(session_id,), daemon=True).start()
            return self.public(session)

    def _read(self, session_id):
        session = self.sessions[session_id]
        process = session["process"]
        for raw in process.stdout:
            line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).strip()
            if not line:
                continue
            with self.lock:
                if line.startswith("OPEN JUPYTERLAB: "):
                    session["url"] = line.partition(": ")[2]
                    session["state"] = "running"
                    if not session["segments"] or session["segments"][-1].get("end"):
                        session["segments"].append({"start": time.time(), "gpu": session["gpu"],
                            "hourly": hourly(self.prices, session["gpu"], session["cpu"], session["ram"])})
                    session["logs"].append("Notebook sẵn sàng. Bấm Mở notebook để làm việc.")
                else:
                    match = re.search(r"https://modal\.com/apps/[^\s]+/(ap-[A-Za-z0-9]+)", line)
                    if match:
                        session["dashboard"], session["app_id"] = match.group(0), match.group(1)
                    match = re.search(r"Starting new session on (T4|L4|A10G|A100|H100)", line)
                    if match:
                        if session["segments"]:
                            session["segments"][-1]["end"] = time.time()
                        session.update(gpu=match.group(1), state="starting", url=None)
                    session["logs"].append(line)
                self.persist()
        code = process.wait()
        with self.lock:
            session.update(state="stopped" if code == 0 or session["stopping"] else "error", ended=time.time(), url=None)
            session["logs"].append(f"Phiên đã dừng (exit {code}).")
            self.persist()

    def stop(self, session_id):
        with self.lock:
            session = self.sessions[session_id]
            if session["state"] in ("stopped", "error"):
                return self.public(session)
            session["stopping"] = True
            session["state"] = "stopping"
            self.persist()
        threading.Thread(target=self._stop, args=(session_id,), daemon=True).start()
        return self.public(session)

    def _stop(self, session_id):
        session = self.sessions[session_id]
        # Ask Jupyter to shut down cleanly first, letting the runner commit its Volume.
        import urllib.request
        if session.get("url"):
            try:
                parsed = urlsplit(session["url"])
                token = parse_qs(parsed.query)["token"][0]
                request = urllib.request.Request(f"{parsed.scheme}://{parsed.netloc}/api/shutdown",
                    data=b"{}", headers={"Authorization": f"token {token}", "Content-Type": "application/json"})
                urllib.request.urlopen(request, timeout=10).close()
                if session.get("process"):
                    session["process"].wait(timeout=25)
                    return
            except Exception as error:
                session["logs"].append(f"Dừng Jupyter: {type(error).__name__}; đang dừng app Modal.")
        if session.get("app_id"):
            command = [str(PYTHON), "-m", "modal", "app", "stop", "-y", session["app_id"]]
            if session.get("profile"):
                command += ["--profile", session["profile"]]
            try:
                result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30,
                    env=self.accounts.environment(self.session_account(session)),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if result.returncode:
                    raise RuntimeError("Modal chưa xác nhận dừng app")
            except Exception as error:
                with self.lock:
                    session.update(state="unknown", stopping=False)
                    session["logs"].append(f"Chưa dừng được: {error}. Mở dashboard Modal để kiểm tra.")
                    self.persist()
                return
        elif session.get("process") and session["process"].poll() is None:
            session["process"].terminate()
            session["process"].wait(timeout=10)
        elif not session.get("process"):
            with self.lock:
                session.update(state="unknown", stopping=False)
                session["logs"].append("Không có App ID để dừng tự động. Kiểm tra dashboard Modal.")
                self.persist()
            return
        with self.lock:
            session.update(state="stopped", ended=time.time(), url=None)
            self.persist()

    def shutdown(self):
        self.billing.stopped.set()
        self.accounts.cancel_login()
        for session in list(self.sessions.values()):
            if session.get("process") and session["process"].poll() is None:
                session["stopping"] = True
                self._stop(session["id"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        host = f"127.0.0.1:{self.server.server_port}"
        navigation = (self.command == "GET" and urlsplit(self.path).path == "/"
                      and self.headers.get("Sec-Fetch-Mode") == "navigate")
        return (self.headers.get("Host") == host
                and self.headers.get("Origin", f"http://{host}") == f"http://{host}"
                and (navigation or self.headers.get("Sec-Fetch-Site") not in ("cross-site", "same-site")))

    def do_GET(self):
        if not self.authorized():
            return self.respond({"error": "Local access only"}, 403)
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/status":
                return self.respond(self.server.manager.status())
            if parsed.path == "/api/folder":
                return self.respond(list_folder(parse_qs(parsed.query).get("path", [str(ROOT)])[0]))
            if parsed.path in ("/", "/app.js", "/style.css"):
                target = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[parsed.path]
                body = (ROOT / "ui/launcher" / target).read_text(encoding="utf-8").replace("__STUDIO_TOKEN__", self.server.token).encode()
                self.send_response(200)
                self.send_header("Content-Type", {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css"}[parsed.path] + "; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)
                return
            self.respond({"error": "Not found"}, 404)
        except (OSError, ValueError) as error:
            self.respond({"error": str(error)}, 400)

    def do_POST(self):
        if not self.authorized() or not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.token):
            return self.respond({"error": "Phiên local không hợp lệ; tải lại trang"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 100000:
                raise ValueError("Request too large")
            value = json.loads(self.rfile.read(size) or b"{}")
            if self.path == "/api/start":
                return self.respond(self.server.manager.start(value))
            if self.path == "/api/accounts/login":
                return self.respond(self.server.manager.accounts.start_login(value["label"]))
            if self.path == "/api/accounts/cancel":
                return self.respond(self.server.manager.accounts.cancel_login())
            if self.path == "/api/accounts/logout":
                return self.respond(self.server.manager.logout_account(value["id"]))
            if self.path == "/api/accounts/default":
                return self.respond(self.server.manager.accounts.set_default(value["id"]))
            if self.path == "/api/accounts/reconnect":
                return self.respond(self.server.manager.accounts.reconnect(value["id"]))
            if self.path == "/api/accounts/verify":
                return self.respond(self.server.manager.accounts.verify(value["id"]))
            if self.path == "/api/accounts/refresh":
                result = self.server.manager.accounts.refresh()
                self.server.manager.billing.request_refresh()
                return self.respond(result)
            if self.path == "/api/billing/refresh":
                self.server.manager.billing.request_refresh()
                return self.respond({"ok": True})
            if self.path == "/api/billing/balance":
                self.server.manager.billing.set_balance(value["id"], value["amount"])
                return self.respond({"ok": True})
            if self.path == "/api/stop":
                return self.respond(self.server.manager.stop(value["id"]))
            if self.path == "/api/files":
                return self.respond(self.server.manager.remote_files(value["id"], value.get("path", "")))
            if self.path == "/api/save-local":
                return self.respond(self.server.manager.save_local(value["id"], value["paths"]))
            if self.path == "/api/open-saved":
                return self.respond(self.server.manager.open_saved_folder(value["id"]))
            if self.path == "/api/prices":
                prices = refresh()
                with self.server.manager.lock:
                    self.server.manager.prices = prices
                return self.respond(prices)
            self.respond({"error": "Not found"}, 404)
        except Exception as error:
            self.respond({"error": str(error)}, 400)


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Open the local Notebook Studio launcher")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    manager = Manager()
    try:
        server = LocalServer(("127.0.0.1", args.port), Handler)
    except OSError:
        url = f"http://127.0.0.1:{args.port}"
        try:
            import urllib.request
            with urllib.request.urlopen(url + "/api/status", timeout=2) as response:
                existing = json.load(response)
            if "projects" in existing and "sessions" in existing:
                print(f"Notebook Studio đang mở: {url}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return
        except Exception:
            pass
        print(f"Cổng {args.port} đang được dùng. Chọn --port khác.", flush=True)
        return
    server.manager, server.token = manager, secrets.token_urlsafe(32)
    if PYTHON.is_file():
        manager.billing.start()
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Notebook Studio: {url}\nGiữ terminal mở. Ctrl+C để dừng server local và các phiên do server này mở.", flush=True)
    atexit.register(manager.shutdown)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.shutdown()
        atexit.unregister(manager.shutdown)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
