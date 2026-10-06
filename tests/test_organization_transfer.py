from contextlib import closing
from pathlib import Path
import copy
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from organizations import OrganizationStore
from organization_transfer import export_configuration, import_configuration


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source = OrganizationStore(root / 'source.db', root, self.no_command)
        self.target = OrganizationStore(root / 'target.db', root, self.no_command)
        self.bundle = {'format': 'herdr-configuration', 'version': 1,
            'organizations': [{'id': 'org', 'name': 'Herdr', 'purpose': 'Build', 'instructions': 'Shared instructions'}],
            'profiles': [
                {'id': 'max', 'organization_id': 'org', 'name': 'Max', 'runtime': 'opencode',
                 'role': 'Backend', 'persona': 'Full persona\nwith Unicode: ü', 'provider': 'opencode',
                 'model': 'big-pickle', 'reasoning': 'high', 'manager_id': '',
                 'permission_mode': 'dashboard_outputs', 'accessible_paths': ['/home/herdr/shared/**'],
                 'project': '/home/herdr/projects/repo', 'use_worktree': True, 'version': 4,
                 'future_setting': {'preserved': True}},
                {'id': 'olaf', 'organization_id': 'org', 'name': 'Olaf', 'runtime': 'codex', 'manager_id': 'max'},
                {'id': 'facilitator', 'organization_id': 'org', 'name': 'Standup', 'runtime': 'opencode', 'group_id': 'group'}],
            'groups': [{'id': 'group', 'organization_id': 'org', 'name': 'Standup',
                        'topic': 'Full multiline\ntopic', 'members': ['max', 'olaf'],
                        'facilitator_id': 'facilitator', 'read_only': True,
                        'permission_mode': 'cli_defaults', 'accessible_paths': [],
                        'use_worktree': False, 'removed_at': '2026-01-01'}]}

    def no_command(self, *args, **kwargs):
        self.fail('Migration must not invoke Herdr or start agents.')

    def tearDown(self):
        self.source.close(); self.target.close(); self.temp.cleanup()

    def test_full_configuration_round_trip_preserves_every_field(self):
        import_configuration(self.source, self.bundle)
        exported = export_configuration(self.source)
        result = import_configuration(self.target, exported)
        restored = export_configuration(self.target)
        for table in ('organizations', 'profiles', 'groups'):
            self.assertEqual(restored[table], self.bundle[table])
            self.assertEqual(result['imported'][table], len(self.bundle[table]))
        with closing(self.target.connect()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertNotIn('jobs', exported)

    def test_collision_and_invalid_references_leave_database_unchanged(self):
        import_configuration(self.target, self.bundle)
        self.assertEqual(import_configuration(self.target, self.bundle)['imported']['profiles'], 3)
        changed = copy.deepcopy(self.bundle)
        changed['profiles'][0]['persona'] = 'Changed after original import'
        with self.assertRaisesRegex(ValueError, 'already exist'):
            import_configuration(self.target, changed)
        bad = copy.deepcopy(self.bundle)
        bad['groups'][0]['members'].append('missing')
        with self.assertRaisesRegex(ValueError, 'reference'):
            import_configuration(self.source, bad)
        self.assertEqual(export_configuration(self.source)['organizations'], [])
        bad = copy.deepcopy(self.bundle)
        bad['profiles'][0]['manager_id'] = 'olaf'
        with self.assertRaisesRegex(ValueError, 'cycle'):
            import_configuration(self.source, bad)
        bad['version'] = 2
        with self.assertRaisesRegex(ValueError, 'version'):
            import_configuration(self.source, bad)

    def test_prompt_field_caps_and_generated_facilitator_exception(self):
        for table, field, limit in (('organizations', 'instructions', 8000),
                                    ('profiles', 'persona', 8000), ('groups', 'description', 8000)):
            bad = copy.deepcopy(self.bundle)
            bad[table][0][field] = 'x' * (limit + 1)
            with self.assertRaisesRegex(ValueError, field):
                import_configuration(self.source, bad)
        good = copy.deepcopy(self.bundle)
        good['profiles'][2]['persona'] = 'x' * 8400
        import_configuration(self.source, good)
        self.assertEqual(export_configuration(self.source)['profiles'][2]['persona'], 'x' * 8400)

    def test_large_export_is_complete_but_flagged_not_importable(self):
        item = copy.deepcopy(self.bundle['organizations'][0])
        item['future_backup_data'] = 'x' * 2_000_001
        with closing(self.source.connect()) as db, db:
            self.source.put(db, 'organizations', item)
        exported = export_configuration(self.source)
        self.assertFalse(exported['import_size_supported'])
        self.assertEqual(exported['organizations'][0], item)
        with self.assertRaisesRegex(ValueError, '2 MB'):
            import_configuration(self.target, exported)

    def test_import_rejects_unlaunchable_model_settings(self):
        for changes in ({'provider': '', 'model': 'big-pickle'},
                        {'provider': 'opencode', 'model': ''},
                        {'provider': 'bad id!', 'model': 'big-pickle'},
                        {'provider': 'opencode', 'model': 'big-pickle', 'reasoning': 'not a level!'}):
            bad = copy.deepcopy(self.bundle)
            bad['profiles'][0].update(changes)
            with self.assertRaises(ValueError):
                import_configuration(self.source, bad)
            self.assertEqual(export_configuration(self.source)['profiles'], [])

    def test_malformed_references_are_rejected_as_value_errors(self):
        cases = []
        bad = copy.deepcopy(self.bundle); bad['groups'][0]['members'] = [['max'], 'olaf']; cases.append(bad)
        bad = copy.deepcopy(self.bundle); bad['profiles'][0]['organization_id'] = ['org']; cases.append(bad)
        bad = copy.deepcopy(self.bundle); bad['profiles'][1]['manager_id'] = ['max']; cases.append(bad)
        bad = copy.deepcopy(self.bundle); bad['profiles'][2]['group_id'] = 7; cases.append(bad)
        bad = copy.deepcopy(self.bundle); bad['groups'][0]['facilitator_id'] = ['facilitator']; cases.append(bad)
        for bad in cases:
            with self.assertRaises(ValueError):
                import_configuration(self.source, bad)
        self.assertEqual(export_configuration(self.source)['profiles'], [])

    def test_imported_groups_keep_the_created_group_member_rules(self):
        for members in ([], ['max'], ['max', 'max'], ['max', 'olaf', 'facilitator']):
            bad = copy.deepcopy(self.bundle)
            bad['groups'][0]['members'] = members
            with self.assertRaisesRegex(ValueError, 'distinct|agents'):
                import_configuration(self.source, bad)
            self.assertEqual(export_configuration(self.source)['groups'], [])
        many = copy.deepcopy(self.bundle)
        many['profiles'] = [{'id': f'p{index}', 'organization_id': 'org', 'name': f'P{index}', 'runtime': 'opencode'}
                            for index in range(7)]
        many['groups'][0].pop('facilitator_id', None)
        many['groups'][0]['members'] = [f'p{index}' for index in range(7)]
        with self.assertRaisesRegex(ValueError, 'distinct'):
            import_configuration(self.source, many)
