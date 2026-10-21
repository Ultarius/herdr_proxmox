"""Deterministic budgets and actual cross-process build admission."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import build_capacity as capacity


class BuildCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.proc, self.cg = self.directory / 'proc', self.directory / 'cg'
        self.proc.mkdir()
        self.cg.mkdir()
        (self.proc / 'meminfo').write_text('MemTotal: 8388608 kB\nMemAvailable: 7340032 kB\nSwapTotal: 4194304 kB\nSwapFree: 0 kB\n')
        for key, value in {'cpu.max': '600000 100000', 'memory.max': str(8 * capacity.GIB),
                           'memory.current': str(capacity.GIB), 'memory.pressure': 'some avg10=0.00 avg60=0.00 total=0\n'}.items():
            (self.cg / key).write_text(value)
        self.lane = self.directory / 'lane'

    def sample(self):
        with patch.object(capacity, 'storage_status', return_value=dict(available=True, blocked=False, reason='')):
            return capacity.budget(self.proc, self.cg, storage=self.directory)

    def test_cgroup_budget_reserves_capacity_and_full_swap_alone_does_not_block(self):
        with patch.object(capacity.os, 'sched_getaffinity', return_value=set(range(16)), create=True):
            budget = self.sample()
        self.assertEqual(budget['compiler_jobs'], 2)
        self.assertEqual(budget['cpu_cores'], 6)
        self.assertEqual(budget['test_threads'], 1)
        self.assertEqual(budget['waiting_reason'], '')
        (self.cg / 'cpu.max').write_text('200000 100000')
        self.assertEqual(self.sample()['compiler_jobs'], 1)
        (self.cg / 'memory.current').write_text(str(8 * capacity.GIB - capacity.GIB // 4))
        self.assertIn('available memory', self.sample()['waiting_reason'])
        (self.cg / 'memory.stat').write_text('inactive_file ' + str(4 * capacity.GIB))
        self.assertEqual(self.sample()['waiting_reason'], '')
        (self.cg / 'memory.pressure').write_text('full avg10=6.00 avg60=0.00 total=0\n')
        self.assertIn('pressure', self.sample()['waiting_reason'])

    def test_parent_cgroup_still_limits_an_unlimited_service_leaf(self):
        (self.proc / 'self').mkdir()
        (self.proc / 'self/cgroup').write_text('0::/service\n')
        service = self.cg / 'service'
        service.mkdir()
        (service / 'memory.max').write_text('max')
        (service / 'cpu.max').write_text('max 100000')
        (self.cg / 'cpu.max').write_text('100000 100000')
        (self.cg / 'memory.max').write_text(str(2 * capacity.GIB))
        self.assertEqual(self.sample()['cpu_cores'], 1)
        self.assertEqual(self.sample()['memory_total'], 2 * capacity.GIB)
        self.assertEqual(self.sample()['compiler_jobs'], 1)

    def test_unknown_capacity_is_conservative_and_explicit_job_flags_are_capped(self):
        with patch.object(capacity, 'storage_status', return_value=dict(available=False, blocked=False, reason='')):
            unknown = capacity.budget(self.directory / 'missing', self.directory / 'missing', storage=self.directory)
        self.assertEqual(unknown['compiler_jobs'], 1)
        self.assertIsNone(unknown['memory_available'])
        for flags in (['-j8'], ['--jobs=8'], ['-j', '8'], ['--jobs', '-1']):
            self.assertEqual(capacity.cargo_arguments(['test', *flags], 2), ['test', '--jobs', '2'])
        self.assertEqual(capacity.cargo_arguments(['test', '-j1', '--', '--test-threads', '12'], 2),
                         ['test', '--jobs', '1', '--', '--test-threads=1'])
        self.assertEqual(capacity.cargo_arguments(['test', '--', '-j8'], 2), ['test', '--', '-j8'])

    def test_disk_and_inode_headroom_block_new_work_without_scanning_or_cleanup(self):
        disk = SimpleNamespace(total=32 * capacity.GIB, free=capacity.GIB)
        inodes = SimpleNamespace(f_files=100000, f_favail=50000)
        with patch.object(capacity.shutil, 'disk_usage', return_value=disk), \
                patch.object(capacity.os, 'statvfs', return_value=inodes, create=True):
            limits = capacity.budget(self.proc, self.cg, storage=self.directory)
            self.assertIn('Low disk space', limits['waiting_reason'])
            self.assertTrue(limits['storage']['blocked'])
            disk.free = 8 * capacity.GIB
            self.assertEqual(capacity.budget(self.proc, self.cg, storage=self.directory)['waiting_reason'], '')
            inodes.f_favail = 0
            self.assertIn('inodes', capacity.budget(self.proc, self.cg, storage=self.directory)['waiting_reason'])
        with patch.object(capacity.shutil, 'disk_usage', side_effect=OSError('unavailable')):
            self.assertFalse(capacity.storage_status(self.directory)['available'])

    def test_disk_wait_never_executes_and_resumes_after_headroom_returns(self):
        changes = []
        def blocked():
            return dict(compiler_jobs=1, waiting_reason='Low disk space.',
                        storage=dict(blocked=True, reason='Low disk space.'))
        with self.assertRaisesRegex(ValueError, 'no checks ran'):
            with capacity.claim(directory=self.lane, sample=blocked, timeout=0, changed=changes.append):
                self.fail('Disk-full work must not execute')
        self.assertEqual(changes[0]['state'], 'waiting')
        ready = False
        def changed(data):
            nonlocal ready
            changes.append(data)
            ready = True
        def sample():
            return dict(compiler_jobs=1, waiting_reason='') if ready else blocked()
        with capacity.claim(directory=self.lane, sample=sample, changed=changed, timeout=2):
            self.assertTrue(ready)
        self.assertEqual(changes[-1]['state'], 'running')

    def child(self, label, hold=.3):
        code = (
            'import sys,time,json; sys.path.insert(0,sys.argv[1]); import build_capacity as c; '
            'sample=lambda:dict(compiler_jobs=1,test_threads=1,waiting_reason=""); '
            '\nwith c.claim("cargo",directory=sys.argv[2],sample=sample,timeout=10):\n'
            ' with open(sys.argv[3],"a") as f: f.write(sys.argv[4]+":start\\n"); f.flush()\n'
            ' time.sleep(float(sys.argv[5]))\n'
            ' with open(sys.argv[3],"a") as f: f.write(sys.argv[4]+":end\\n")\n'
        )
        process = subprocess.Popen([sys.executable, '-c', code, str(Path(capacity.__file__).parent),
                                    str(self.lane), str(self.directory / 'events'), label, str(hold)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(predicate())

    def test_actual_processes_share_one_fifo_lane_and_report_waiting(self):
        first = self.child('first', .8)
        self.wait_for(lambda: (self.lane / 'active.json').exists())
        second = self.child('second', .2)
        self.wait_for(lambda: len(list(self.lane.glob('wait-*.json'))) == 1)
        third = self.child('third', .1)
        self.wait_for(lambda: len(list(self.lane.glob('wait-*.json'))) == 2)
        state = capacity.snapshot(self.lane)
        self.assertIsNotNone(state['active'])
        self.assertEqual(state['waiting'], 2)
        for process in (first, second, third):
            output, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors.decode())
        self.assertEqual((self.directory / 'events').read_text().splitlines(),
                         ['first:start', 'first:end', 'second:start', 'second:end', 'third:start', 'third:end'])
        self.assertIsNone(capacity.snapshot(self.lane)['active'])

    def test_memory_pressure_wait_is_not_execution_and_timeout_cleans_ticket(self):
        changes = []
        sample = lambda: dict(compiler_jobs=1, test_threads=1, waiting_reason='Waiting for available memory.')
        with self.assertRaisesRegex(ValueError, 'no checks ran'):
            with capacity.claim(directory=self.lane, sample=sample, timeout=0, changed=changes.append):
                self.fail('No work may run under pressure')
        self.assertEqual(changes[0]['state'], 'waiting')
        self.assertEqual(list(self.lane.glob('wait-*.json')), [])
        with capacity.claim(directory=self.lane, sample=lambda: dict(compiler_jobs=1, waiting_reason='')):
            pass

    def test_dead_owner_releases_kernel_lock_without_replaying_work(self):
        process = self.child('lost', 30)
        self.wait_for(lambda: (self.lane / 'active.json').exists())
        process.kill()
        process.communicate(timeout=5)
        with capacity.claim(directory=self.lane, sample=lambda: dict(compiler_jobs=1, waiting_reason=''), timeout=2):
            pass
        self.assertEqual((self.directory / 'events').read_text().splitlines(), ['lost:start'])

    @unittest.skipIf(sys.platform == 'win32', 'Windows validators do not hold a parent lane descriptor.')
    def test_gateway_checks_wait_for_agent_lane_then_receive_budget(self):
        from check_runner import run_scripts
        owner = self.child('agent', .9)
        self.wait_for(lambda: (self.lane / 'active.json').exists())
        folder = self.directory / 'checks'
        folder.mkdir()
        marker = self.directory / 'executed'
        code = ('import os,pathlib; pathlib.Path(' + repr(str(marker)) + ').write_text('
                'os.environ["HERDR_BUILD_JOBS"]+":"+os.environ["HERDR_TEST_THREADS"]); print("actual checks")')
        commands = [dict(id='fixture', arguments=[sys.executable, '-c', code], cwd='.')]
        changed, result, errors = [], [], []
        def execute():
            try:
                result.append(run_scripts(self.directory, commands, folder, .7, 'bash', 20000, changed.append))
            except Exception as error:
                errors.append(error)
        real_claim = capacity.claim
        with patch.dict(os.environ, HERDR_BUILD_CAPACITY_ROOT=str(self.lane)), \
                patch.object(capacity, 'budget', return_value=dict(compiler_jobs=1, test_threads=1, waiting_reason='')), \
                patch.object(capacity, 'claim', side_effect=lambda **kwargs: real_claim(timeout=4, **kwargs)):
            worker = threading.Thread(target=execute)
            worker.start()
            try:
                self.wait_for(lambda: bool(changed))
                self.assertEqual(changed[0][0]['phase'], 'waiting_capacity')
                self.assertFalse(marker.exists())
                self.assertIn('queued', (folder / 'log.txt').read_text())
                owner.communicate(timeout=5)
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.communicate(timeout=5)
                worker.join(6)
                self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0][1:], (0, False))
        self.assertIn(marker.read_text(), ('1:1', '2:1'))
        self.assertIn('actual checks', (folder / 'log.txt').read_text())
        self.assertIn('Herdr build queued', (folder / 'log.txt').read_text())

    def test_windows_validator_uses_budget_without_holding_child_lane(self):
        import check_runner
        limits = dict(compiler_jobs=1, storage=dict(blocked=False))
        with patch.object(check_runner.sys, 'platform', 'win32'), \
                patch.object(capacity, 'claim') as claim, \
                patch.object(capacity, 'budget', return_value=limits) as budget, \
                patch.object(check_runner, '_run_scripts', return_value=([], 0, False)) as execute:
            self.assertEqual(check_runner.run_scripts(self.directory, [], self.directory, 1, 'bash', 20000), ([], 0, False))
            claim.assert_not_called()
            budget.assert_called_once_with(storage=self.directory)
            self.assertEqual(execute.call_args.args[-1], dict(fd=None, budget=limits))
            limits['storage'] = dict(blocked=True, reason='Low disk space.')
            with self.assertRaisesRegex(ValueError, 'No checks ran'):
                check_runner.run_scripts(self.directory, [], self.directory, 1, 'bash', 20000)
            self.assertEqual(execute.call_count, 1)

    def test_real_validation_commands_receive_adapter_scoped_budget_variables(self):
        from check_runner import run_scripts
        for name in (None, 'python', 'node', 'dotnet-sdk', 'rust-toolchain'):
            folder = self.directory / (name or 'generic')
            folder.mkdir()
            observed = folder / 'environment.json'
            code = ('import os,json,pathlib; pathlib.Path(' + repr(str(observed)) + ').write_text('
                    'json.dumps({key:os.environ.get(key) for key in '
                    '["HERDR_BUILD_JOBS","HERDR_TEST_THREADS","CARGO_BUILD_JOBS","RUST_TEST_THREADS"]}))')
            commands = [dict(id='fixture', arguments=[sys.executable, '-c', code], cwd='.',
                             bindings=[dict(name=name, executables=[])] if name else [])]
            with self.subTest(toolchain=name), patch.dict(os.environ, HERDR_BUILD_CAPACITY_ROOT=str(self.lane)), \
                    patch.object(capacity, 'budget', return_value=dict(compiler_jobs=1, waiting_reason='', storage=dict(blocked=False))):
                os.environ.pop('CARGO_BUILD_JOBS', None)
                os.environ.pop('RUST_TEST_THREADS', None)
                checks, status, timed_out = run_scripts(self.directory, commands, folder, 10, 'bash', 20000)
            self.assertEqual(status, 0, checks)
            self.assertFalse(timed_out)
            actual = json.loads(observed.read_text())
            self.assertEqual(actual['HERDR_BUILD_JOBS'], '1')
            self.assertEqual(actual['HERDR_TEST_THREADS'], '1')
            self.assertEqual(actual['CARGO_BUILD_JOBS'], '1' if name == 'rust-toolchain' else None)
            self.assertEqual(actual['RUST_TEST_THREADS'], '1' if name == 'rust-toolchain' else None)

    def test_capacity_logging_failure_does_not_abort_wait_or_lose_queue_message(self):
        import check_runner
        @contextmanager
        def claim(**kwargs):
            kwargs['changed'](dict(state='waiting', reason='Waiting for available memory.'))
            yield dict(fd=None, budget=dict(compiler_jobs=1))
        folder = self.directory / 'checks'
        folder.mkdir()
        # No commands are needed: the final log must still retain the queue phase.
        with patch.object(capacity, 'claim', side_effect=claim), \
                patch.object(check_runner.sys, 'platform', 'linux'), \
                patch.object(Path, 'write_bytes', side_effect=OSError('disk full')):
            self.assertEqual(check_runner.run_scripts(self.directory, [], folder, 1, 'bash', 20000), ([], 1, False))
        with patch.object(capacity, 'claim', side_effect=claim), patch.object(check_runner.sys, 'platform', 'linux'):
            check_runner.run_scripts(self.directory, [], folder, 1, 'bash', 20000)
        self.assertIn('Herdr build queued', (folder / 'log.txt').read_text())
        self.assertIn('Herdr build running', (folder / 'log.txt').read_text())

    def test_health_snapshot_does_not_create_or_prune_files_and_probes_requested_storage(self):
        self.lane.mkdir()
        ticket = self.lane / 'wait-stale.json'
        ticket.write_text(json.dumps(dict(pid=0, process_start='', created_ns=1)))
        with patch.object(capacity, 'budget', return_value={}) as budget, patch.object(capacity, '_live', return_value=False):
            self.assertEqual(capacity.snapshot(self.lane, storage=self.directory)['waiting'], 0)
            budget.assert_called_once_with(storage=self.directory)
        self.assertEqual(list(self.lane.iterdir()), [ticket])

    def test_cargo_help_and_nonheavy_commands_bypass_admission_and_missing_arguments_are_legible(self):
        with self.assertRaisesRegex(ValueError, 'Expected the build lane'):
            capacity.main([])
        with patch.object(capacity.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'No Cargo executable'):
                capacity.main([str(self.lane), 'build'])
        class Executed(Exception):
            pass
        with patch.object(capacity.shutil, 'which', return_value='real-cargo'), \
                patch.object(capacity.os, 'execv', side_effect=Executed) as execute, \
                patch.object(capacity, 'claim') as claim:
            for arguments in (['build', '--help'], ['test', '--version'], ['--help'], ['fmt']):
                with self.subTest(arguments=arguments), self.assertRaises(Executed):
                    capacity.main([str(self.lane), *arguments])
                execute.assert_called_with('real-cargo', ['real-cargo', *arguments])
            claim.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Symlink creation requires extra Windows privileges.')
    def test_install_refuses_symlinked_wrapper_targets(self):
        scratch = self.directory / 'scratch'
        scratch.mkdir()
        commands = scratch / 'commands'
        commands.mkdir()
        target = self.directory / 'untouched'
        target.write_text('keep')
        (commands / 'cargo').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            capacity.install_cargo(scratch, lane=self.lane)
        self.assertEqual(target.read_text(), 'keep')

    @unittest.skipUnless(os.name != 'nt', 'POSIX inherited descriptors and executable scripts.')
    def test_nested_disk_refusal_does_not_wait_on_or_release_the_parent_lease(self):
        with capacity.claim(directory=self.lane, sample=lambda: dict(compiler_jobs=1, waiting_reason='')) as lease:
            with patch.dict(os.environ, HERDR_BUILD_LANE_FD=str(lease['fd'])):
                with self.assertRaisesRegex(ValueError, 'No new command ran'):
                    with capacity.claim(directory=self.lane, sample=lambda: dict(compiler_jobs=1,
                            storage=dict(blocked=True, reason='Low disk space.'))):
                        self.fail('A nested command must not start on a full disk')
            self.assertIsNotNone(capacity.snapshot(self.lane)['active'])

    @unittest.skipUnless(os.name != 'nt', 'POSIX inherited descriptors and executable scripts.')
    def test_nested_cargo_wrapper_inherits_lane_and_preserves_exit_status(self):
        scratch = self.directory / 'scratch'
        scratch.mkdir()
        commands = capacity.install_cargo(scratch, lane=self.lane)
        real = self.directory / 'real'
        real.mkdir()
        cargo = real / 'cargo'
        cargo.write_text('#!/bin/sh\necho "$CARGO_BUILD_JOBS:$RUST_TEST_THREADS:$*"\nexit 7\n')
        cargo.chmod(0o700)
        with capacity.claim(directory=self.lane, sample=lambda: dict(compiler_jobs=1, test_threads=1, waiting_reason='')) as lease:
            env = dict(os.environ, PATH=str(commands) + os.pathsep + str(real) + os.pathsep + os.environ['PATH'],
                       **capacity.variables(lease))
            process = subprocess.run([str(commands / 'cargo'), 'test', '-j8'], env=env,
                                     pass_fds=(lease['fd'],), capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 7, process.stderr)
            self.assertIn('1:1:test --jobs 1', process.stdout)


if __name__ == '__main__':
    unittest.main()
