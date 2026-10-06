from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from resource_usage import ResourceUsage


class ResourceUsageTests(unittest.TestCase):
    def test_container_limits_cpu_deltas_and_processes(self):
        with tempfile.TemporaryDirectory() as root:
            proc, cg = Path(root) / 'proc', Path(root) / 'cg'
            proc.mkdir(); cg.mkdir()
            (proc / 'meminfo').write_text('MemTotal: 10000 kB\nMemAvailable: 9000 kB\nSwapTotal: 2000 kB\nSwapFree: 1000 kB\n')
            for key, value in {'memory.max': '4096', 'memory.current': '3900',
                               'memory.swap.max': '1024', 'memory.swap.current': '900',
                               'cpu.max': '100000 100000', 'cpu.stat': 'usage_usec 1000000'}.items():
                (cg / key).write_text(value)
            pid = proc / '42'; pid.mkdir()
            fields = ['0'] * 22
            fields[0] = 'R'; fields[11] = '100'; fields[19] = '50'; fields[21] = '3'
            (pid / 'stat').write_text('42 (opencode) ' + ' '.join(fields))
            monitor = ResourceUsage(proc, cg)
            first = monitor.sample(10)
            self.assertEqual(first['memory_total'], 4096)
            self.assertEqual(first['memory_used'], 3900)
            self.assertEqual(first['swap_used'], 900)
            self.assertIsNone(first['cpu_percent'])
            self.assertEqual(first['processes'][0]['name'], 'opencode')
            self.assertNotIn('args', first['processes'][0])
            (cg / 'cpu.stat').write_text('usage_usec 6000000')
            second = monitor.sample(20)
            self.assertAlmostEqual(second['cpu_percent'], 50)
            self.assertEqual(second['processes'][0]['cpu_percent'], 0)
            fields[19] = '51'
            (pid / 'stat').write_text('42 (opencode) ' + ' '.join(fields))
            self.assertIsNone(monitor.sample(30)['processes'][0]['cpu_percent'])

    def test_unavailable_proc_is_explicit_and_cached(self):
        with tempfile.TemporaryDirectory() as root:
            monitor = ResourceUsage(Path(root) / 'missing', root)
            data = monitor.snapshot()
            self.assertFalse(data['available'])
            self.assertIs(monitor.snapshot(), data)

    def test_missing_memtotal_returns_unavailable_instead_of_keyerror(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'meminfo').write_text('MemFree: 10 kB\n')
            self.assertFalse(ResourceUsage(root, root).snapshot()['available'])
