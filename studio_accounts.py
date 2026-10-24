"""Local Modal account registry. Credentials stay in Modal TOML files, never in UI JSON."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import tomllib
from urllib.parse import urlsplit
import uuid

CREDENTIAL_ENV = ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MODAL_OAUTH_REFRESH_TOKEN",
                  "MODAL_OAUTH_CLIENT_ID", "MODAL_OAUTH_CLIENT_SECRET")


def clean_modal_env(base=None):
    environment = dict(os.environ if base is None else base)
    for key in (*CREDENTIAL_ENV, "MODAL_PROFILE", "MODAL_CONFIG_PATH", "MODAL_ENVIRONMENT"):
        environment.pop(key, None)
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return environment


def read_config(path):
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Accounts:
    def __init__(self, store, python, *, config_path=None):
        self.store, self.python = Path(store), str(python)
        self.folder = self.store / "accounts"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.store / "accounts.json"
        self.lock = threading.RLock()
        self.records = {}
        self.login = None
        self.default = None
        self.external_config = Path(config_path or os.environ.get("MODAL_CONFIG_PATH") or Path.home() / ".modal.toml").resolve()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.records, self.default = payload["records"], payload.get("default")
        except (OSError, ValueError, KeyError):
            pass
        self.discover()

    def _persist(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"records": self.records, "default": self.default}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def discover(self):
        with self.lock:
            for profile, values in read_config(self.external_config).items():
                if not isinstance(values, dict) or not (values.get("token_secret") or values.get("oauth_refresh_token")):
                    continue
                identity = hashlib.sha256(f"{self.external_config}\0{profile}".encode()).hexdigest()[:16]
                if identity not in self.records:
                    self.records[identity] = {"id": identity, "label": profile, "profile": profile,
                        "config": str(self.external_config), "source": "CLI", "enabled": True, "workspace": None}
                if self.default is None and values.get("active") and self.records[identity]["enabled"]:
                    self.default = identity
            if self.default is None:
                self.default = next((r["id"] for r in self.records.values() if self.connected(r)), None)
            self._persist()

    def connected(self, record):
        if not record.get("enabled"):
            return False
        values = read_config(record["config"]).get(record["profile"], {})
        return bool(values.get("token_secret") or values.get("oauth_refresh_token"))

    def status(self):
        with self.lock:
            accounts = [{key: record.get(key) for key in ("id", "label", "profile", "source", "workspace", "verified_at")}
                        | {"connected": self.connected(record), "default": record["id"] == self.default}
                        for record in self.records.values()]
            login = None
            if self.login:
                login = {key: self.login.get(key) for key in ("id", "label", "state", "url", "code", "message", "account_id")}
            return {"items": accounts, "default": self.default, "login": login}

    def resolve(self, account_id=None, *, profile=None):
        with self.lock:
            if account_id:
                record = self.records.get(account_id)
            elif profile:
                matches = [r for r in self.records.values() if r["profile"] == profile and self.connected(r)]
                if len(matches) != 1:
                    raise ValueError("Chọn rõ tài khoản Modal cho phiên này")
                record = matches[0]
            else:
                record = self.records.get(self.default)
            if not record or not self.connected(record):
                raise ValueError("Tài khoản chưa đăng nhập. Mở mục Tài khoản để đăng nhập hoặc kết nối lại")
            return dict(record)

    def environment(self, record, base=None):
        environment = clean_modal_env(base)
        environment.update(MODAL_CONFIG_PATH=record["config"], MODAL_PROFILE=record["profile"])
        return environment

    def set_default(self, account_id):
        record = self.resolve(account_id)
        with self.lock:
            self.default = record["id"]
            self._persist()
        return self.status()

    def reconnect(self, account_id):
        with self.lock:
            record = self.records[account_id]
            record["enabled"] = True
            if not self.connected(record):
                record["enabled"] = False
                raise ValueError("Thông tin đăng nhập đã được xóa; bấm Thêm tài khoản để đăng nhập lại")
            if self.default is None:
                self.default = account_id
            self._persist()
        return self.status()

    def logout(self, account_id):
        with self.lock:
            record = self.records[account_id]
            # CLI profiles belong to the user's existing setup. Disconnect them
            # from Studio without deleting unrelated CLI configuration.
            if record["source"] == "Studio":
                Path(record["config"]).unlink(missing_ok=True)
            record["enabled"] = False
            if self.default == account_id:
                self.default = next((r["id"] for r in self.records.values() if self.connected(r)), None)
            self._persist()
        return self.status()

    def verify(self, account_id):
        record = self.resolve(account_id)
        result = subprocess.run([self.python, "-X", "utf8", "-m", "modal", "profile", "list", "--json"],
            env=self.environment(record), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            rows = json.loads(result.stdout)
            row = next(row for row in rows if row["name"] == record["profile"])
            workspace = row["workspace"]
            if result.returncode or workspace.startswith("Unknown"):
                raise ValueError
        except (ValueError, KeyError, StopIteration):
            raise ValueError("Không xác minh được tài khoản. Kiểm tra mạng hoặc đăng nhập lại") from None
        with self.lock:
            self.records[account_id].update(workspace=workspace, verified_at=time.time())
            self._persist()
        return self.status()

    def refresh(self):
        self.discover()
        with self.lock:
            groups = {}
            for record in self.records.values():
                if self.connected(record):
                    groups.setdefault(record["config"], dict(record))
        for config, record in groups.items():
            try:
                result = subprocess.run([self.python, "-X", "utf8", "-m", "modal", "profile", "list", "--json"],
                    env=self.environment(record), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                rows = json.loads(result.stdout)
                if result.returncode:
                    continue
                with self.lock:
                    for row in rows:
                        if row["workspace"].startswith("Unknown"):
                            continue
                        for target in self.records.values():
                            if target["config"] == config and target["profile"] == row["name"]:
                                target.update(workspace=row["workspace"], verified_at=time.time())
                    self._persist()
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                continue
        return self.status()

    def start_login(self, label):
        label = str(label).strip()
        if not label or len(label) > 80 or any(ord(c) < 32 for c in label):
            raise ValueError("Đặt tên tài khoản từ 1 đến 80 ký tự")
        with self.lock:
            if self.login and self.login["state"] == "waiting":
                raise ValueError("Đang có một lần đăng nhập. Hoàn tất hoặc hủy trước")
            login_id = uuid.uuid4().hex
            config = self.folder / f"{login_id}.toml"
            record = {"id": login_id, "label": label, "profile": "studio", "config": str(config),
                      "source": "Studio", "enabled": True, "workspace": None}
            environment = self.environment(record)
            environment.update(BROWSER="none", COLUMNS="240", NO_COLOR="1")
            process = subprocess.Popen([self.python, "-X", "utf8", "-m", "modal", "token", "new", "--no-activate"],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.login = {"id": login_id, "label": label, "state": "waiting", "url": None, "code": None,
                          "message": "Đang tạo liên kết đăng nhập chính thức của Modal…", "process": process,
                          "config": str(config)}
            login = self.login
        threading.Thread(target=self._login_worker, args=(login, record), daemon=True).start()
        return self.status()

    def _login_worker(self, login, record):
        process = login["process"]
        timer = threading.Timer(600, lambda: self.cancel_login(login["id"]))
        timer.daemon = True
        timer.start()
        try:
            for raw in process.stdout:
                # Never expose the CLI's raw authentication output or tokens.
                line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)
                match = re.search(r"https://modal\.com/[^\s\x1b\[\]]+", line)
                with self.lock:
                    if login["state"] != "waiting":
                        continue
                    if match:
                        url = match.group(0).rstrip(".,)")
                        if urlsplit(url).hostname == "modal.com":
                            login.update(url=url, message="Mở trang Modal, chọn đúng tài khoản rồi hoàn tất xác thực.")
                    code = re.search(r"Enter this code:\s*([A-Za-z0-9-]+)", line)
                    if code:
                        login["code"] = code.group(1)
            code = process.wait()
            with self.lock:
                if login["state"] != "waiting":
                    return
                if code != 0 or not self.connected(record):
                    raise ValueError("Đăng nhập chưa hoàn tất. Thử lại trên trang Modal")
                self.records[record["id"]] = record
                if not self.default:
                    self.default = record["id"]
                self._persist()
                login.update(state="complete", account_id=record["id"], url=None, code=None,
                             message="Đã đăng nhập. Chọn tài khoản này khi mở phiên notebook.")
            try:
                self.verify(record["id"])
            except Exception:
                pass
        except Exception:
            with self.lock:
                if login["state"] == "waiting":
                    login.update(state="error", url=None, code=None, message="Không đăng nhập được; thử lại hoặc kiểm tra mạng.")
                    Path(record["config"]).unlink(missing_ok=True)
        finally:
            timer.cancel()

    def cancel_login(self, login_id=None):
        with self.lock:
            login = self.login
            if not login or login["state"] != "waiting" or (login_id and login["id"] != login_id):
                return self.status()
            login.update(state="cancelled", url=None, code=None, message="Đã hủy đăng nhập.")
            process = login["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        Path(login["config"]).unlink(missing_ok=True)
        return self.status()
