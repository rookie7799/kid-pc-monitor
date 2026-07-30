import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pc_control


def make_control(start_time, usage_limit=120, current_user="Child"):
    """Build a monitor without starting Windows-specific background threads."""
    control = pc_control.PCTimeControl.__new__(pc_control.PCTimeControl)
    control.lock_times = []
    control.usage_limit = usage_limit
    control.start_time = start_time
    control.current_user = current_user
    control.warnings_sent = set()
    control.logger = Mock()
    control.save_state = Mock()
    return control


class TimeLimitTests(unittest.TestCase):
    def test_time_remaining_uses_elapsed_minutes(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(minutes=30), usage_limit=120)

        self.assertAlmostEqual(control.get_time_remaining(now), 90.0)

    def test_daily_limit_resets_on_new_day(self):
        now = datetime(2026, 7, 30, 0, 5, 0)
        control = make_control(datetime(2026, 7, 29, 20, 0, 0), usage_limit=60)
        control.warnings_sent = {"15min", "5min"}

        reached, _ = control.check_time_limits(now)

        self.assertFalse(reached)
        self.assertEqual(control.start_time, now)
        self.assertEqual(control.warnings_sent, set())
        control.save_state.assert_called_once_with()

    def test_exempt_user_is_never_limited(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=4), usage_limit=1, current_user="ParentAdmin")

        with patch.object(pc_control, "EXEMPT_USERS", ["ParentAdmin"]):
            reached, reason = control.check_time_limits(now)

        self.assertFalse(reached)
        self.assertEqual(reason, "")


class DataDirectoryTests(unittest.TestCase):
    def test_uses_local_appdata_for_installed_windows_agent(self):
        data_dir = pc_control.resolve_data_dir({
            "LOCALAPPDATA": r"C:\Users\Kids\AppData\Local",
        })

        self.assertEqual(
            data_dir,
            Path(r"C:\Users\Kids\AppData\Local") / "KidPCMonitor",
        )

    def test_explicit_data_directory_override_takes_precedence(self):
        data_dir = pc_control.resolve_data_dir({
            "KID_PC_MONITOR_DATA_DIR": r"D:\AgentData",
            "LOCALAPPDATA": r"C:\Users\Kids\AppData\Local",
        })

        self.assertEqual(data_dir, Path(r"D:\AgentData"))


if __name__ == "__main__":
    unittest.main()
