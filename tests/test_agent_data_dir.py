"""Where the Windows agent keeps its mutable files.

Installed code lives under Program Files, where a child account has no write
access, so state and logs must go to a per-user directory. Run with
`python -m unittest discover -s tests` or pytest.
"""
import sys
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


if __name__ == '__main__':
    unittest.main()
