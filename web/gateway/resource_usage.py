"""Cheap Linux container metrics; no subprocesses or process arguments exposed."""
import os
from pathlib import Path
import threading
import time


class ResourceUsage:
    def __init__(self, proc='/proc', cgroup='/sys/fs/cgroup'):
        self.proc, self.cgroup = Path(proc), Path(cgroup)
        self.lock = threading.Lock()
        self.previous = None
        self.processes = {}
        self.cached = None
        self.checked = 0

    def snapshot(self):
        with self.lock:
            stamp = time.monotonic()
            if self.cached is not None and stamp - self.checked < 5:
                return self.cached
            try:
                data = self.sample(stamp)
            except (OSError, ValueError, IndexError, KeyError):
                data = {'available': False, 'error': 'Container metrics unavailable.'}
            self.cached, self.checked = data, stamp
            return data

    def sample(self, stamp):
        memory = {}
        for line in (self.proc / 'meminfo').read_text().splitlines():
            parts = line.split()
            memory[parts[0].rstrip(':')] = int(parts[1]) * 1024
        total = memory['MemTotal']
        used = total - memory.get('MemAvailable', memory.get('MemFree', 0))
        swap_total = memory.get('SwapTotal', 0)
        swap_used = swap_total - memory.get('SwapFree', 0)
        # LXC may expose host meminfo: finite cgroup limits take precedence.
        def value(name):
            return (self.cgroup / name).read_text().strip()
        try:
            limit = value('memory.max')
            if limit != 'max' and int(limit) <= total:
                total, used = int(limit), int(value('memory.current'))
            swap_limit = value('memory.swap.max')
            if swap_limit != 'max':
                swap_total, swap_used = int(swap_limit), int(value('memory.swap.current'))
        except (OSError, ValueError):
            pass
        cores = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
        cpu = None
        try:
            quota, period = value('cpu.max').split()
            if quota != 'max':
                cores = min(cores, int(quota) / int(period))
            usage = dict(line.split() for line in value('cpu.stat').splitlines())
            seconds = int(usage['usage_usec']) / 1_000_000
            if self.previous and stamp > self.previous[0]:
                cpu = max(0, min(100, (seconds - self.previous[1]) / (stamp - self.previous[0]) / cores * 100))
            self.previous = (stamp, seconds)
        except (OSError, ValueError, KeyError, ZeroDivisionError):
            pass
        rows, current = [], {}
        ticks = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
        page = os.sysconf('SC_PAGE_SIZE') if hasattr(os, 'sysconf') else 4096
        for directory in self.proc.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                stat = (directory / 'stat').read_text()
                left, right = stat.index('('), stat.rindex(')')
                name = stat[left + 1:right]
                fields = stat[right + 2:].split()
                seconds = (int(fields[11]) + int(fields[12])) / ticks
                key = (directory.name, fields[19])  # start time prevents PID-reuse spikes
                percent = None
                if key in self.processes and stamp > self.processes[key][0]:
                    percent = max(0, (seconds - self.processes[key][1]) / (stamp - self.processes[key][0]) * 100)
                current[key] = (stamp, seconds)
                rows.append({'pid': int(directory.name), 'name': name[:80],
                             'memory_bytes': max(0, int(fields[21])) * page,
                             'cpu_percent': percent})
            except (OSError, ValueError, IndexError):
                continue
        self.processes = current
        rows.sort(key=lambda row: row['memory_bytes'], reverse=True)
        return {'available': True, 'sampled_at': time.time(), 'cpu_percent': cpu,
                'cpu_cores': cores, 'memory_used': used, 'memory_total': total,
                'swap_used': swap_used, 'swap_total': swap_total, 'processes': rows[:20]}


resources = ResourceUsage()
