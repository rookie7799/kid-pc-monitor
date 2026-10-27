"""Tests for the daily budget accounting in the Windows agent.

The daily limit is a budget of *active, unlocked* usage, not wall-clock time
since the limit was set: a locked screen, a parent's session, sleep/hibernate
and the midnight reset must not consume the child's time.

Run with `python -m unittest discover -s tests` (or pytest).
"""
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pc_control  # noqa: E402


def make_control(start_time, usage_limit=120, current_user='Child',
                 accrued_seconds=0.0):
    """Build a monitor without starting the Windows-specific threads."""
    control = pc_control.PCTimeControl.__new__(pc_control.PCTimeControl)
    control.usage_limit = usage_limit
    control.start_time = start_time
    control.accrued_seconds = accrued_seconds
    control.last_saved_accrued = accrued_seconds
    control.current_user = current_user
    control.is_locked = False
    control.lock_times = []
    control.warnings_sent = set()
    control.last_remaining = None
    control.state_lock = pc_control.threading.RLock()
    control.logger = Mock()
    control.save_state = Mock()
    return control


class UsageAccountingTests(unittest.TestCase):
    def test_time_remaining_counts_accrued_usage_only(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        # Wall clock says 4 hours since the limit was set, but only 30 minutes
        # of that was actual unlocked usage.
        control = make_control(now - timedelta(hours=4), usage_limit=120,
                               accrued_seconds=30 * 60)

        self.assertAlmostEqual(control.get_time_remaining(now), 90.0)

    def test_time_remaining_ignores_wall_clock_while_locked(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=5), usage_limit=120,
                               accrued_seconds=30 * 60)

        self.assertAlmostEqual(control.get_time_remaining(now), 90.0)

    def test_exhausted_budget_reports_zero_not_negative(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=4), usage_limit=120,
                               accrued_seconds=200 * 60)

        self.assertEqual(control.get_time_remaining(now), 0)

    def test_exhausted_budget_locks(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=4), usage_limit=120,
                               accrued_seconds=130 * 60)

        reached, reason = control.check_time_limits(now)

        self.assertTrue(reached)
        self.assertIn('Usage limit', reason)

    def test_exempt_user_is_never_limited(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(now - timedelta(hours=4), usage_limit=1,
                               current_user='ParentAdmin', accrued_seconds=3600)

        with patch.object(pc_control, 'EXEMPT_USERS', ['ParentAdmin']):
            reached, reason = control.check_time_limits(now)
            remaining = control.get_time_remaining(now)

        self.assertFalse(reached)
        self.assertEqual(reason, '')
        self.assertIsNone(remaining)

    def test_accrual_pauses_while_locked(self):
        control = make_control(datetime(2026, 7, 30, 18, 0, 0))
        control.is_locked = True

        self.assertFalse(control._should_accrue_time())

    def test_accrual_pauses_for_an_exempt_user(self):
        control = make_control(datetime(2026, 7, 30, 18, 0, 0),
                               current_user='ParentAdmin')

        with patch.object(pc_control, 'EXEMPT_USERS', ['ParentAdmin']):
            self.assertFalse(control._should_accrue_time())

    def test_accrual_runs_for_a_monitored_user_at_the_desktop(self):
        control = make_control(datetime(2026, 7, 30, 18, 0, 0))

        self.assertTrue(control._should_accrue_time())


class SampleGapTests(unittest.TestCase):
    """A poll gap is usage only when the loop ran on schedule the whole time."""

    def test_normal_poll_counts_in_full(self):
        self.assertEqual(
            pc_control.PCTimeControl._active_sample_seconds(
                3.0, datetime(2026, 7, 30).date(), datetime(2026, 7, 30).date()),
            3.0)

    def test_sleep_or_hibernate_gap_is_not_charged(self):
        # 2 hours of suspended time between two polls.
        self.assertEqual(
            pc_control.PCTimeControl._active_sample_seconds(
                7200.0, datetime(2026, 7, 30).date(), datetime(2026, 7, 30).date()),
            0.0)

    def test_gap_across_midnight_is_dropped(self):
        self.assertEqual(
            pc_control.PCTimeControl._active_sample_seconds(
                3.0, datetime(2026, 7, 30).date(), datetime(2026, 7, 31).date()),
            0.0)

    def test_negative_elapsed_is_never_charged(self):
        self.assertEqual(
            pc_control.PCTimeControl._active_sample_seconds(
                -5.0, datetime(2026, 7, 30).date(), datetime(2026, 7, 30).date()),
            0.0)


class DailyResetTests(unittest.TestCase):
    def test_check_time_limits_rolls_the_day_over(self):
        now = datetime(2026, 7, 30, 0, 5, 0)
        control = make_control(datetime(2026, 7, 29, 20, 0, 0), usage_limit=60,
                               accrued_seconds=60 * 60)
        control.warnings_sent = {'15min', '5min'}

        reached, _ = control.check_time_limits(now)

        self.assertFalse(reached)
        self.assertEqual(control.start_time, now)
        self.assertEqual(control.accrued_seconds, 0.0)
        self.assertEqual(control.warnings_sent, set())
        control.save_state.assert_called_once_with()

    def test_same_day_does_not_reset(self):
        now = datetime(2026, 7, 30, 18, 0, 0)
        control = make_control(datetime(2026, 7, 30, 8, 0, 0), usage_limit=60,
                               accrued_seconds=600.0)

        self.assertFalse(control.reset_daily_usage_if_needed(now))
        self.assertEqual(control.accrued_seconds, 600.0)
        control.save_state.assert_not_called()


class StatePersistenceTests(unittest.TestCase):
    def _control_for(self, state_file):
        control = pc_control.PCTimeControl.__new__(pc_control.PCTimeControl)
        control.usage_limit = None
        control.lock_times = []
        control.accrued_seconds = 0.0
        control.last_saved_accrued = 0.0
        control.start_time = datetime.now()
        control.current_user = 'Kids'
        control.state_file = str(state_file)
        control.logger = Mock()
        return control

    def test_load_state_restores_accrued_usage(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'pc_control_state.json'
            state_file.write_text(json.dumps({
                'usage_limit': 120,
                'accrued_seconds': 42.5,
                'start_time': datetime.now().replace(microsecond=0).isoformat(),
                'current_user': 'Kids',
            }))

            control = self._control_for(state_file)
            control.load_state()

        self.assertEqual(control.accrued_seconds, 42.5)
        self.assertEqual(control.last_saved_accrued, 42.5)

    def test_load_state_zeroes_usage_carried_over_from_yesterday(self):
        # Regression: yesterday's exhausted budget must not stick to the new day.
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'pc_control_state.json'
            yesterday = datetime.now() - timedelta(days=1)
            state_file.write_text(json.dumps({
                'usage_limit': 360,
                'accrued_seconds': 21600.0,
                'start_time': yesterday.isoformat(),
                'current_user': 'Kids',
            }))

            control = self._control_for(state_file)
            control.load_state()

        self.assertEqual(control.accrued_seconds, 0.0)
        self.assertEqual(control.start_time.date(), datetime.now().date())

    def test_legacy_state_keeps_the_usage_already_spent_today(self):
        # A state file written before usage was tracked independently of
        # start_time has no accrued_seconds. Upgrading mid-day must not hand the
        # child a fresh budget.
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'pc_control_state.json'
            started = datetime.now() - timedelta(minutes=30)
            state_file.write_text(json.dumps({
                'usage_limit': 120,
                'start_time': started.isoformat(),
                'current_user': 'Kids',
            }))

            control = self._control_for(state_file)
            control.load_state()

        self.assertAlmostEqual(control.accrued_seconds, 30 * 60, delta=5)

    def test_save_state_writes_accrued_usage_and_updates_the_flush_mark(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'pc_control_state.json'
            control = self._control_for(state_file)
            control.accrued_seconds = 91.25

            control.save_state()
            saved = json.loads(state_file.read_text())

        self.assertEqual(saved['accrued_seconds'], 91.2)
        self.assertEqual(control.last_saved_accrued, 91.25)


class AccrualPersistenceTests(unittest.TestCase):
    """Usage must be flushed on a fixed schedule, not when a modulo happens to hit."""

    def test_saves_every_save_interval_of_accumulated_usage(self):
        control = make_control(datetime(2026, 7, 30, 18, 0, 0), usage_limit=120)

        control._accrue_usage(29.0)
        self.assertEqual(control.save_state.call_count, 0)

        control._accrue_usage(1.0)
        self.assertEqual(control.save_state.call_count, 1)

        control._accrue_usage(29.0)
        self.assertEqual(control.save_state.call_count, 1)
        control._accrue_usage(1.0)
        self.assertEqual(control.save_state.call_count, 2)

    def test_irregular_steps_still_hit_the_interval(self):
        # The old `int(accrued) % 30 == 0` check almost never landed here.
        control = make_control(datetime(2026, 7, 30, 18, 0, 0), usage_limit=120)

        for _ in range(40):
            control._accrue_usage(3.0)

        self.assertEqual(control.save_state.call_count, 4)

    def test_interval_is_measured_from_the_loaded_value(self):
        control = make_control(datetime(2026, 7, 30, 18, 0, 0), usage_limit=120,
                               accrued_seconds=600.0)

        control._accrue_usage(29.0)
        self.assertEqual(control.save_state.call_count, 0)
        control._accrue_usage(1.0)
        self.assertEqual(control.save_state.call_count, 1)

    def test_no_limit_means_no_accrual_and_no_writes(self):
        control = make_control(datetime(2026, 7, 30, 18, 0, 0), usage_limit=None)

        control._accrue_usage(120.0)

        self.assertEqual(control.accrued_seconds, 0.0)
        self.assertEqual(control.save_state.call_count, 0)


class ServerCommandTests(unittest.TestCase):
    def _server(self):
        server = pc_control.RemoteControlServer.__new__(pc_control.RemoteControlServer)
        # __del__ calls stop_server(); give it the state a real instance has.
        server.clients = {}
        server.running = False
        server.server_socket = None
        server.pc_control = make_control(datetime(2026, 7, 30, 18, 0, 0),
                                         usage_limit=60, accrued_seconds=1800.0)
        server.logger = Mock()
        return server

    def test_set_limit_starts_a_fresh_budget(self):
        server = self._server()

        response = server.process_command('SET_LIMIT:120')

        self.assertIn('120', response)
        self.assertEqual(server.pc_control.usage_limit, 120)
        self.assertEqual(server.pc_control.accrued_seconds, 0.0)
        self.assertEqual(server.pc_control.last_saved_accrued, 0.0)
        server.pc_control.save_state.assert_called_once_with()

    def test_extend_time_keeps_the_usage_already_spent(self):
        server = self._server()

        server.process_command('EXTEND_TIME:30')

        self.assertEqual(server.pc_control.usage_limit, 90)
        self.assertEqual(server.pc_control.accrued_seconds, 1800.0)


if __name__ == '__main__':
    unittest.main()
