"""Container resource sampling with an explicit process fallback."""
from pathlib import Path
import subprocess
import time

import psutil


class ResourceSampler:
    def __init__(self, cpu_cores=4, memory_gib=16):
        self.cached = {}
        self.sampled_at = 0
        self.cpu_sample = None
        self.process_cpu_sample = None
        self.cpu_cores, self.memory_gib = cpu_cores, memory_gib

    def read(self):
        now = time.monotonic()
        if now - self.sampled_at < 2:
            return self.cached
        # Modal's isolated runtime may expose host /proc totals without cgroups.
        # In that case show process RSS/CPU relative to this session's allocation.
        rss = cpu_seconds = 0
        for process in psutil.process_iter():
            try:
                rss += process.memory_info().rss
                cpu_time = process.cpu_times()
                cpu_seconds += cpu_time.user + cpu_time.system
            except (psutil.Error, OSError):
                continue
        ram_total = self.memory_gib * 1024 ** 3
        metrics = {"ram_used": rss, "ram_total": ram_total,
                   "ram_percent": min(100, rss / ram_total * 100), "cpu_percent": None,
                   "cpu_scope": "container", "gpu_percent": None,
                   "vram_used_mb": None, "vram_total_mb": None}
        cgroup = Path("/sys/fs/cgroup")
        try:
            used = int((cgroup / "memory.current").read_text())
            limit = int((cgroup / "memory.max").read_text())
            metrics.update(ram_used=used, ram_total=limit, ram_percent=used / limit * 100)
        except (OSError, ValueError, ZeroDivisionError):
            metrics["ram_scope"] = "process"
        else:
            metrics["ram_scope"] = "container"
        try:
            usage = dict(line.split() for line in (cgroup / "cpu.stat").read_text().splitlines())
            quota, period = (cgroup / "cpu.max").read_text().split()
            cores = int(quota) / int(period) if quota != "max" else psutil.cpu_count()
            cpu = int(usage["usage_usec"])
            if self.cpu_sample:
                old_time, old_cpu = self.cpu_sample
                metrics["cpu_percent"] = min(100, max(0, (cpu - old_cpu) / 1e6 / (now - old_time) / cores * 100))
            else:
                metrics["cpu_percent"] = None
            self.cpu_sample = (now, cpu)
        except (OSError, ValueError, KeyError, ZeroDivisionError):
            metrics["cpu_scope"] = "process"
            if self.process_cpu_sample:
                old_time, old_cpu = self.process_cpu_sample
                metrics["cpu_percent"] = min(100, max(0, (cpu_seconds - old_cpu) / (now - old_time) / self.cpu_cores * 100))
        self.process_cpu_sample = (now, cpu_seconds)
        try:
            query = subprocess.run([
                "nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ], capture_output=True, text=True, timeout=2, check=True)
            load, used, total = map(float, query.stdout.strip().splitlines()[0].split(","))
            metrics.update(gpu_percent=load, vram_used_mb=used, vram_total_mb=total)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        self.cached, self.sampled_at = metrics, now
        return metrics
