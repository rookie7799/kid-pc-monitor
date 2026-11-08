"""Where the Windows agent keeps its mutable files.

Installed code lives under Program Files, where a child account has no write
access, so state and logs must go to a per-user directory. Run with
`python -m unittest discover -s tests` or pytest.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pc_control  # noqa: E402


class DataDirectoryTests(unittest.TestCase):
    def test_uses_local_appdata_for_installed_windows_agent(self):
        data_dir = pc_control.resolve_data_dir({
            'LOCALAPPDATA': r'C:\Users\Kids\AppData\Local',
        })

        self.assertEqual(
            data_dir,
            Path(r'C:\Users\Kids\AppData\Local') / 'KidPCMonitor',
        )

    def test_explicit_data_directory_override_takes_precedence(self):
        data_dir = pc_control.resolve_data_dir({
            'KID_PC_MONITOR_DATA_DIR': r'D:\AgentData',
            'LOCALAPPDATA': r'C:\Users\Kids\AppData\Local',
        })

        self.assertEqual(data_dir, Path(r'D:\AgentData'))

    def test_falls_back_to_the_current_directory_without_local_appdata(self):
        self.assertEqual(pc_control.resolve_data_dir({}), Path.cwd())


class LegacyStateMigrationTests(unittest.TestCase):
    """An upgrade must keep the settings that used to live next to the code."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.legacy_dir = self.root / 'src'
        self.legacy_dir.mkdir()
        self.new_dir = self.root / 'AppData' / 'Local' / 'KidPCMonitor'
        self.target = self.new_dir / pc_control.STATE_FILE_NAME

    def _write_legacy_state(self):
        state = {
            'lock_times': ['21:00'],
            'usage_limit': 60,
            'accrued_seconds': 1200.0,
            'start_time': '2026-10-27T08:00:00',
            'current_user': 'Kids',
        }
        legacy_file = self.legacy_dir / pc_control.STATE_FILE_NAME
        legacy_file.write_text(json.dumps(state), encoding='utf-8')
        return legacy_file, state

    def test_copies_state_from_the_old_location_on_upgrade(self):
        legacy_file, state = self._write_legacy_state()

        migrated = pc_control.migrate_legacy_state_file(
            self.target, legacy_dirs=[self.legacy_dir]
        )

        self.assertEqual(migrated, legacy_file)
        self.assertTrue(self.target.is_file())
        self.assertEqual(json.loads(self.target.read_text(encoding='utf-8')), state)

    def test_existing_state_file_is_never_overwritten(self):
        self._write_legacy_state()
        self.new_dir.mkdir(parents=True)
        current = {'usage_limit': 30, 'lock_times': ['20:00'], 'accrued_seconds': 0.0}
        self.target.write_text(json.dumps(current), encoding='utf-8')

        migrated = pc_control.migrate_legacy_state_file(
            self.target, legacy_dirs=[self.legacy_dir]
        )

        self.assertIsNone(migrated)
        self.assertEqual(json.loads(self.target.read_text(encoding='utf-8')), current)

    def test_nothing_to_migrate_without_a_legacy_file(self):
        migrated = pc_control.migrate_legacy_state_file(
            self.target, legacy_dirs=[self.legacy_dir]
        )

        self.assertIsNone(migrated)
        self.assertFalse(self.target.exists())

    def test_legacy_path_equal_to_target_is_not_copied_onto_itself(self):
        self.new_dir.mkdir(parents=True)
        state = {'usage_limit': 45, 'lock_times': [], 'accrued_seconds': 0.0}
        self.target.write_text(json.dumps(state), encoding='utf-8')

        migrated = pc_control.migrate_legacy_state_file(
            self.target, legacy_dirs=[self.new_dir]
        )

        self.assertIsNone(migrated)


if __name__ == '__main__':
    unittest.main()
