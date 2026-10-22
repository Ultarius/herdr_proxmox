"""Administrative storage observations and retention, outside request polling.

Only exact registry identities can be reclaimed. A preview is not an activity
permit: ownership, generations, active executions and kernel leases are checked
again by the manager immediately before deletion.
"""
import copy
import logging
import threading
import time
from contextlib import contextmanager

import build_capacity
import execution_storage
from workspace_storage import StorageBusy


class StorageService:
    def __init__(self, organizations, validation, *, manager=None, interval=300):
        self.organizations, self.validation = organizations, validation
        self.manager = manager or execution_storage.manager(projects=organizations.projects)
        self.interval = interval
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.data = dict(available=True, sampled_at=None, scanning=False, inventory={})

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='workspace-storage', daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=10)

    def active_ids(self):
        """Missing activity authority is uncertainty, never permission to collect."""
        try:
            jobs = self.organizations.job_records()
            result = {job['id'] for job in jobs if job.get('kind') == 'launch' and
                      not ((job.get('storage_preparation') or {}).get('state') == 'failed' and
                           (job.get('storage_preparation') or {}).get('no_agent_started') is True and not job.get('agent_session')) and
                      not (job.get('session_closed_at') and job.get('state') in ('finished', 'released') and
                           (not job.get('storage_binding') or (job.get('storage_finish') or {}).get('state') == 'complete'))}
            active_profiles = {job.get('profile_id') for job in jobs if job.get('state') in
                               ('queued', 'running', 'waiting_for_input', 'waiting_environment')}
            result.update(job['id'] for job in jobs if job.get('kind') == 'launch' and
                          job.get('profile_id') in active_profiles)
            runs = self.validation.storage_records()
            if not isinstance(runs, list):
                return None
            result.update(run['id'] for run in runs if run.get('state') in ('queued', 'running') or
                          (run.get('storage_finish') or {}).get('state') == 'pending')
            return result
        except Exception:
            logging.exception('Workspace storage activity lookup failed; reclamation disabled')
            return None

    def _closed_executions(self):
        errors = []
        for job in self.organizations.job_records(launches_only=True):
            try:
                if job.get('storage_binding') and job.get('session_closed_at') and job.get('state') in ('finished', 'released'):
                    if (job.get('storage_finish') or {}).get('state') != 'complete':
                        result = execution_storage.finish(job['storage_binding'], job.get('worktree_path') or
                            job.get('source_project'), close=True)
                        self.manager.close_execution(job['id'])
                        self.organizations.update_job(job['id'], storage_finish=dict(state='complete', warnings=result.get('errors', [])))
                    else:
                        self.manager.close_execution(job['id'])
                preparation = job.get('storage_preparation') or {}
                if preparation.get('state') == 'failed' and preparation.get('no_agent_started') is True and not job.get('agent_session'):
                    self.manager.close_execution(job['id'])
            except Exception as error:
                self._pending(errors, 'agent', job['id'], error,
                    lambda value: self.organizations.update_job(job['id'], storage_finish=value))
        for run in self.validation.storage_records():
            if run.get('state') in ('complete', 'failed', 'error', 'cancelled', 'interrupted'):
                try:
                    self.manager.close_execution(run['id'])
                    if (run.get('storage_finish') or {}).get('state') == 'pending':
                        self.validation.update_storage_finish(run['id'], dict(state='complete'))
                except Exception as error:
                    self._pending(errors, 'validation', run['id'], error,
                        lambda value: self.validation.update_storage_finish(run['id'], value))
        return errors

    @staticmethod
    def _pending(errors, lane, identity, error, persist):
        message = str(error)[:500]
        errors.append(dict(lane=lane, execution_id=identity, error=message))
        logging.warning('Storage finalization pending for %s %s: %s', lane, identity, message)
        try:
            persist(dict(state='pending', error=message))
        except Exception:
            logging.exception('Could not persist pending storage finalization for %s', identity)

    def snapshot(self):
        # No directory walks, Git calls or runtime queries on a dashboard poll.
        with self.lock:
            result = copy.deepcopy(self.data)
        result['policy'] = execution_storage.settings(self.manager.root)
        import environment_adapters
        mode = result['policy']['build_mode']
        result['build_guidance'] = [dict(capability=name, mode=mode, text=text)
            for name, adapter in environment_adapters.ADAPTERS.items()
            if (text := dict(adapter.build_guidance).get(mode))]
        return result

    def scan(self, *, maintain=False, wait=False):
        if not self.operation.acquire(blocking=wait):
            return self.snapshot()
        with self.lock:
            self.data['scanning'] = True
        try:
            errors = self._closed_executions()
            policy = execution_storage.settings(self.manager.root)
            if maintain:
                with build_capacity.try_idle() as idle:
                    pressure = any(build_capacity.storage_status(path).get('blocked') is True
                                   for path in (self.manager.projects, self.manager.root))
                    self.manager.maintain(active_execution_ids=self.active_ids, pressure=pressure,
                        kinds=None if idle else {'download-cache'},
                        **{key: policy[key] for key in ('output_budget_bytes', 'shared_budget_bytes',
                                                       'max_closed_run_bytes', 'idle_seconds')})
            inventory = self.manager.inventory(self.active_ids)
            with self.lock:
                self.data = dict(available=True, sampled_at=time.time(), scanning=False, inventory=inventory,
                                 finalization_errors=errors)
        except Exception as error:
            logging.exception('Workspace storage observation failed')
            with self.lock:
                self.data = dict(available=False, sampled_at=time.time(), scanning=False,
                                 inventory={}, error=str(error)[:500])
        finally:
            self.operation.release()
        return self.snapshot()

    def run(self):
        while not self.stop.is_set():
            self.scan(maintain=True)
            self.wake.wait(self.interval)
            self.wake.clear()

    def action(self, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a storage action object.')
        action = body.get('action')
        allowed = {'scan': {'action'}, 'preview': {'action', 'ids'},
                   'reclaim': {'action', 'ids', 'expected'}, 'configure': {'action', *execution_storage.DEFAULTS},
                   'legacy-preview': {'action', 'execution_id', 'project'},
                   'legacy-adopt': {'action', 'preview', 'reviewed'},
                   'forget-invalid': {'action', 'id', 'row_digest', 'reviewed'}}
        if action not in allowed or set(body) - allowed[action]:
            raise ValueError('Unknown storage action or field.')
        if action == 'scan':
            with self.lock:
                self.data['scanning'] = True
            # Explicit observation does not request an extra retention pass.
            threading.Thread(target=lambda: self.scan(wait=True), name='storage-scan', daemon=True).start()
            return dict(snapshot=self.snapshot())
        if action == 'configure':
            execution_storage.configure_settings({key: value for key, value in body.items() if key != 'action'}, self.manager.root)
            return dict(snapshot=self.snapshot())
        if action == 'forget-invalid':
            with self.action_lock():
                result = self.manager.forget_invalid(body.get('id'), body.get('row_digest'), reviewed=body.get('reviewed') is True)
            return dict(result=result, snapshot=self.scan())
        if action == 'legacy-preview':
            with build_capacity.try_idle() as idle:
                if not idle:
                    raise StorageBusy('A build is active. Wait for it to finish before reviewing existing output.')
                return dict(preview=self.manager.preview_legacy(body.get('execution_id'), project=body.get('project'),
                                                               active_execution_ids=self.active_ids))
        if action == 'legacy-adopt':
            with build_capacity.try_idle() as idle:
                if not idle:
                    raise StorageBusy('A build is active. Wait for it to finish before registering existing output.')
                result = self.manager.adopt_legacy(body.get('preview'), reviewed=body.get('reviewed') is True,
                                                   active_execution_ids=self.active_ids)
            return dict(result=result, snapshot=self.scan())
        ids = body.get('ids')
        if not isinstance(ids, list) or not ids or len(ids) > 200 or any(not isinstance(value, str) for value in ids):
            raise ValueError('Select between one and 200 registered storage identities.')
        with self.action_lock():
            if action == 'preview':
                inventory = self.manager.inventory(self.active_ids, ids=ids)
                entries = {entry['id']: entry for entry in inventory['entries']}
                if any(identity not in entries or not entries[identity]['eligible'] for identity in ids):
                    raise ValueError('Selected storage is active or unavailable; scan it again.')
                preview = dict(ids=list(dict.fromkeys(ids)), expected={identity: entries[identity]['generation'] for identity in ids},
                               bytes=sum(entries[identity]['bytes'] for identity in set(ids)),
                               entries=[dict(id=identity, path=entries[identity]['path'], bytes=entries[identity]['bytes'])
                                        for identity in dict.fromkeys(ids)])
                return dict(preview=preview, snapshot=self.snapshot())
            expected = body.get('expected')
            if not isinstance(expected, dict) or set(expected) != set(ids) or any(type(value) is not int for value in expected.values()):
                raise ValueError('Cleanup requires the exact preview generations.')
            with build_capacity.try_idle() as idle:
                if not idle:
                    raise StorageBusy('A build is active. Wait for it to finish before cleanup.')
                result = self.manager.reclaim(ids, active_execution_ids=self.active_ids, dry_run=False, expected=expected)
        return dict(result=result, snapshot=self.scan())

    @contextmanager
    def action_lock(self):
        if not self.operation.acquire(blocking=False):
            raise StorageBusy('Storage maintenance is busy. Wait for the current scan or cleanup to finish.')
        try:
            yield
        finally:
            self.operation.release()
