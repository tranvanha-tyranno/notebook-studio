from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui/jupyter"))
from ca2_metrics import ResourceSampler


class ResourceTests(unittest.TestCase):
    def test_process_fallback_uses_session_allocation_not_host_ram(self):
        process = MagicMock()
        process.memory_info.return_value.rss = 1024 ** 3
        process.cpu_times.return_value = MagicMock(user=2, system=1)
        with patch("ca2_metrics.time.monotonic", side_effect=[100, 103]), \
             patch("ca2_metrics.psutil.process_iter", return_value=[process]), \
             patch("ca2_metrics.Path.read_text", side_effect=FileNotFoundError), \
             patch("ca2_metrics.subprocess.run", return_value=MagicMock(stdout="25, 1000, 15000\n")):
            sampler = ResourceSampler(cpu_cores=2, memory_gib=8)
            first = sampler.read()
            self.assertEqual(first["ram_scope"], "process")
            self.assertEqual(first["ram_total"], 8 * 1024 ** 3)
            self.assertAlmostEqual(first["ram_percent"], 12.5)
            self.assertIsNone(first["cpu_percent"])
            process.cpu_times.return_value = MagicMock(user=4, system=1)
            second = sampler.read()
            self.assertAlmostEqual(second["cpu_percent"], 100 / 3)
            self.assertEqual(second["gpu_percent"], 25)
            self.assertEqual(second["vram_total_mb"], 15000)

    def test_missing_gpu_metrics_are_unknown_not_zero(self):
        with patch("ca2_metrics.psutil.process_iter", return_value=[]), \
             patch("ca2_metrics.Path.read_text", side_effect=FileNotFoundError), \
             patch("ca2_metrics.subprocess.run", side_effect=FileNotFoundError):
            result = ResourceSampler().read()
            self.assertIsNone(result["gpu_percent"])
            self.assertIsNone(result["vram_total_mb"])


if __name__ == "__main__":
    unittest.main()
