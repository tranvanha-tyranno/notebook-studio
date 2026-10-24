"""Public Modal list prices, in USD per second. No account/billing access."""
from datetime import datetime, timezone
import re
import urllib.request

SOURCE = "https://modal.com/pricing"
SNAPSHOT = {
    "source": SOURCE, "checked_at": "2026-10-24T04:25:00+07:00",
    "gpu": {"T4": 0.000164, "L4": 0.000222, "A10G": 0.000306,
            "A100": 0.000583, "H100": 0.001097},
    "cpu": 0.0000131, "ram": 0.00000222,
}


def hourly(prices, gpu, cpu, ram):
    return (prices["gpu"][gpu] + cpu * prices["cpu"] + ram * prices["ram"]) * 3600


def refresh():
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "NotebookStudio/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read(3_000_000).decode()
    text = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    start = text.index("Resource costs")
    text = text[start:text.index("Pricing plans", start)]
    def rate(label):
        match = re.search(re.escape(label) + r"\s*\$\s*([0-9.]+)", text)
        if not match:
            raise ValueError(f"Không đọc được giá {label}; giữ bảng giá trước đó")
        value = float(match.group(1))
        if not 0 < value < 0.02:
            raise ValueError("Bảng giá thay đổi định dạng; hãy đối chiếu nguồn Modal")
        return value
    return {"source": SOURCE, "checked_at": datetime.now(timezone.utc).isoformat(),
            "gpu": {key: rate(label) for key, label in {
                "T4": "Nvidia T4", "L4": "Nvidia L4", "A10G": "Nvidia A10",
                "A100": "Nvidia A100, 40 GB", "H100": "Nvidia H100 SXM5",
            }.items()}, "cpu": rate("CPU"), "ram": rate("Memory")}
