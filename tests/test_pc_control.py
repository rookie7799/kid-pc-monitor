import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pc_control


def make_control(start_time, usage_limit=120, current_user="Child", accrued_seconds=0.0,
                 allowed_start=None, allowed_end=None):
    """Build a monitor without starting Windows-specific background threads."""
    control = pc_control.PCTimeControl.__new__(pc_control.PCTimeControl)
    control.usage_limit = usage_limit
    control.start_time = start_time
    control.accrued_seconds = accrued_seconds
    control.current_user = current_user
    control.warnings_sent = set()
    control.allowed_start = allowed_start or pc_control.dtime(7, 0)
    control.allowed_end = allowed_end or pc_control.dtime(22, 0)
    control.unlock_until = None
    control.logger = Mock()
    control.save_state = Mock()
    return control


class TimeLimitTests(unittest.TestCase):
    def test_time_remaining_counts_active_usage_only(self):
        # 120-min limit, only 30 min of active (unlocked) usage accrued.
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=4), usage_limit=120,
                               accrued_seconds=30 * 60)

        self.assertAlmostEqual(control.get_time_remaining(now), 90.0)

    def test_time_remaining_ignores_wall_clock_while_locked(self):
        # Wall clock advanced 5 hours but only 30 min were active -> still 90 left.
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=5), usage_limit=120,
                               accrued_seconds=30 * 60)

        self.assertAlmostEqual(control.get_time_remaining(now), 90.0)

    def test_daily_limit_resets_on_new_day(self):
        # Use a full-day window so the daily-reset behaviour is isolated from
        # the allowed-window lock (00:05 is outside the default 07:00-22:00).
        now = datetime(2026, 7, 30, 0, 5, 0)
        control = make_control(datetime(2026, 7, 29, 20, 0, 0), usage_limit=60,
                               allowed_start=pc_control.dtime(0, 0),
                               allowed_end=pc_control.dtime(23, 59))
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


class AllowedWindowTests(unittest.TestCase):
    def test_within_default_window_is_allowed(self):
        # Default 07:00-22:00, at 18:00 the PC is allowed.
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now)
        reached, _ = control.check_time_limits(now)
        self.assertFalse(reached)

    def test_early_morning_outside_window_is_locked(self):
        # Default 07:00-22:00, at 05:00 the PC must be locked.
        now = datetime(2026, 7, 30, 5, 0, 0)
        control = make_control(now)
        reached, reason = control.check_time_limits(now)
        self.assertTrue(reached)
        self.assertIn("window", reason)

    def test_night_after_window_end_is_locked(self):
        # Default 07:00-22:00, at 23:00 the PC must be locked.
        now = datetime(2026, 7, 30, 23, 0, 0)
        control = make_control(now)
        reached, _ = control.check_time_limits(now)
        self.assertTrue(reached)

    def test_usage_limit_reached_inside_window_is_locked(self):
        # Inside the window, an exhausted daily limit still locks the PC.
        now = datetime(2026, 7, 30, 12, 0, 0)
        control = make_control(now, usage_limit=120, accrued_seconds=130 * 60)
        reached, reason = control.check_time_limits(now)
        self.assertTrue(reached)
        self.assertIn("Usage limit", reason)

    def test_cross_midnight_window_allows_early_morning(self):
        # Window 22:00-07:00 (crosses midnight): at 06:00 it is allowed.
        now = datetime(2026, 7, 30, 6, 0, 0)
        control = make_control(now, allowed_start=pc_control.dtime(22, 0),
                               allowed_end=pc_control.dtime(7, 0))
        reached, _ = control.check_time_limits(now)
        self.assertFalse(reached)

    def test_temporary_unlock_allows_outside_window(self):
        # Window 07:00-22:00, at 23:00 normally locked, but unlock_until grants access.
        now = datetime(2026, 7, 30, 23, 0, 0)
        control = make_control(now)
        control.unlock_until = now + timedelta(minutes=30)
        reached, _ = control.check_time_limits(now)
        self.assertFalse(reached)

    def test_temporary_unlock_does_not_bypass_daily_limit(self):
        # Even with an active unlock, an exhausted daily limit still locks.
        now = datetime(2026, 7, 30, 23, 0, 0)
        control = make_control(now, usage_limit=120, accrued_seconds=130 * 60)
        control.unlock_until = now + timedelta(minutes=30)
        reached, reason = control.check_time_limits(now)
        self.assertTrue(reached)
        self.assertIn("Usage limit", reason)

    def test_temporary_unlock_expires_and_relocks(self):
        # After unlock_until passes, the window constraint applies again.
        now = datetime(2026, 7, 30, 23, 0, 0)
        control = make_control(now)
        control.unlock_until = now + timedelta(minutes=30)
        later = now + timedelta(minutes=45)
        reached, _ = control.check_time_limits(later)
        self.assertTrue(reached)
        self.assertIsNone(control.unlock_until)  # expired override cleared


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
