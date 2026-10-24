"""Workspace billing cache; Modal credentials are isolated in helper subprocesses."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import threading
import time


def amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise ValueError("Số tiền USD không hợp lệ") from None


def summary_payload(workspace, summary):
    credits = next((value for key, value in summary.adjustments.items() if key.casefold() == "credits"), None)
    return {"workspace": workspace, "cycle": summary.start.strftime("%Y-%m"),
        "metered": str(summary.metered_cost), "billed": str(summary.billed_cost),
        "credits_used": str(max(Decimal(0), -credits)) if credits is not None else None}


class Billing:
    def __init__(self, accounts):
        self.accounts = accounts
        self.path = accounts.store / "billing.json"
        self.lock = threading.RLock()
        self.refreshing = False
        self.errors = {}
        self.stopped = threading.Event()
        try:
            self.workspaces = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.workspaces = {}

    def _persist(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.workspaces, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def fetch(self, account_id):
        record = self.accounts.resolve(account_id)
        result = subprocess.run([self.accounts.python, "-X", "utf8", str(Path(__file__).resolve()), "--read"],
            env=self.accounts.environment(record), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            if result.returncode:
                raise ValueError
            data = json.loads(result.stdout)
            if not data["workspace"] or not data["cycle"]:
                raise ValueError
            for key in ("metered", "billed"):
                data[key] = str(amount(data[key]))
            if data["credits_used"] is not None:
                data["credits_used"] = str(amount(data["credits_used"]))
        except (KeyError, TypeError, ValueError):
            # Raw SDK output can contain auth information; never expose it.
            raise ValueError("Không đọc được billing. Kiểm tra mạng, quyền Billing hoặc đăng nhập lại.") from None
        with self.accounts.lock:
            current = self.accounts.resolve(account_id)
            if (current["config"], current["profile"]) != (record["config"], record["profile"]):
                raise ValueError("Tài khoản đã thay đổi; cập nhật lại billing")
            self.accounts.records[account_id].update(workspace=data["workspace"], verified_at=time.time())
            self.accounts._persist()
            with self.lock:
                previous = self.workspaces.get(data["workspace"], {})
                data.update(updated_at=time.time(), reference=previous.get("reference"))
                self.workspaces[data["workspace"]] = data
                self.errors.pop(account_id, None)
                self._persist()
        return data

    def public(self, record):
        with self.lock:
            data = dict(self.workspaces.get(record.get("workspace"), {}))
            data.update(error=self.errors.get(record["id"]), refreshing=self.refreshing, balance=None)
            reference = data.pop("reference", None)
            if reference:
                data["balance_recorded_at"] = reference["at"]
                used = data.get("credits_used")
                valid = (data.get("cycle") == reference["cycle"] == datetime.now(timezone.utc).strftime("%Y-%m")
                         and used is not None and amount(used) >= amount(reference["credits_used"]))
                if valid:
                    data["balance"] = str(max(Decimal(0), amount(reference["amount"]) - (amount(used) - amount(reference["credits_used"]))))
                else:
                    data["balance_message"] = "Kỳ billing/credits đã thay đổi; nhập lại số dư từ Modal."
            return data

    def set_balance(self, account_id, value):
        balance = amount(value)
        if balance < 0 or balance > Decimal("1000000000"):
            raise ValueError("Số dư phải từ 0 đến 1 tỷ USD")
        data = self.fetch(account_id)
        if data["credits_used"] is None:
            raise ValueError("Modal chưa cung cấp credits đã dùng, chưa thể theo dõi số dư ước tính")
        with self.lock:
            self.workspaces[data["workspace"]]["reference"] = {
                "amount": str(balance), "credits_used": data["credits_used"], "cycle": data["cycle"], "at": time.time()}
            self._persist()

    def refresh(self):
        try:
            seen = set()
            for record in self.accounts.status()["items"]:
                if self.stopped.is_set():
                    break
                if not record["connected"] or (record["workspace"] and record["workspace"] in seen):
                    continue
                try:
                    data = self.fetch(record["id"])
                    seen.add(data["workspace"])
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    with self.lock:
                        self.errors[record["id"]] = "Không cập nhật được billing; dữ liệu cũ được giữ lại. Kiểm tra mạng/quyền Billing."
        finally:
            with self.lock:
                self.refreshing = False

    def request_refresh(self):
        with self.lock:
            if not self.refreshing:
                self.refreshing = True
                threading.Thread(target=self.refresh, daemon=True).start()

    def start(self):
        def loop():
            while not self.stopped.is_set():
                self.request_refresh()
                self.stopped.wait(300)
        threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    import modal
    workspace = modal.Workspace.from_context()
    summary = workspace.billing.summary()
    print(json.dumps(summary_payload(workspace.name, summary)))
