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
                 accrued_seconds=0.0, allowed_start=None, allowed_end=None):
    """Build a monitor without starting the Windows-specific threads."""
    control = pc_control.PCTimeControl.__new__(pc_control.PCTimeControl)
    control.usage_limit = usage_limit
    control.start_time = start_time
    control.accrued_seconds = accrued_seconds
    control.last_saved_accrued = accrued_seconds
    control.current_user = current_user
    # A fresh refresh mark keeps the PowerShell console-user lookup out of tests.
    control.last_user_refresh = pc_control.time.monotonic()
    control._get_console_user = Mock(return_value=None)
    control.is_locked = False
    control.lock_times = []
    control.allowed_start = allowed_start
    control.allowed_end = allowed_end
    control.unlock_until = None
    control.warnings_sent = set()
    control.warning_intervals = [15, 5, 1]
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
        control.last_user_refresh = pc_control.time.monotonic()
        control._get_console_user = Mock(return_value=None)
        control.allowed_start = None
        control.allowed_end = None
        control.unlock_until = None
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



class AllowedWindowTests(unittest.TestCase):
    """The availability window is opt-in: unset means "always allowed"."""

    def test_no_window_configured_allows_any_hour(self):
        # Default state: an existing install must not start locking at night
        # just because the agent was upgraded.
        night = datetime(2026, 7, 30, 3, 0, 0)
        control = make_control(night - timedelta(hours=1), usage_limit=240)

        self.assertTrue(control.is_within_allowed_window(night))
        reached, reason = control.check_time_limits(night)
        self.assertFalse(reached)
        self.assertEqual(reason, '')

    def test_outside_the_window_locks(self):
        night = datetime(2026, 7, 30, 5, 0, 0)
        control = make_control(night, usage_limit=240,
                               allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0))

        reached, reason = control.check_time_limits(night)

        self.assertTrue(reached)
        self.assertIn('window', reason)

    def test_late_evening_outside_the_window_locks(self):
        night = datetime(2026, 7, 30, 23, 0, 0)
        control = make_control(night, usage_limit=240,
                               allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0))

        self.assertTrue(control.check_time_limits(night)[0])

    def test_inside_the_window_is_allowed(self):
        noon = datetime(2026, 7, 30, 12, 0, 0)
        control = make_control(noon, usage_limit=240,
                               allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0))

        self.assertFalse(control.check_time_limits(noon)[0])

    def test_window_crossing_midnight(self):
        # 22:00-07:00 is the "evening and early morning" window.
        control = make_control(datetime(2026, 7, 30, 6, 0, 0), usage_limit=240,
                               allowed_start=pc_control.dtime(22, 0),
                               allowed_end=pc_control.dtime(7, 0))

        self.assertFalse(control.check_time_limits(
            datetime(2026, 7, 30, 6, 0, 0))[0])
        self.assertFalse(control.check_time_limits(
            datetime(2026, 7, 30, 23, 30, 0))[0])
        self.assertTrue(control.check_time_limits(
            datetime(2026, 7, 30, 12, 0, 0))[0])

    def test_exhausted_limit_still_locks_inside_the_window(self):
        noon = datetime(2026, 7, 30, 12, 0, 0)
        control = make_control(noon, usage_limit=120, accrued_seconds=130 * 60,
                               allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0))

        reached, reason = control.check_time_limits(noon)

        self.assertTrue(reached)
        self.assertIn('Usage limit', reason)

    def test_scheduled_lock_time_wins_inside_the_window(self):
        # Bedtime locks are a separate feature and stay enforced.
        control = make_control(datetime(2026, 7, 30, 20, 0, 0), usage_limit=240,
                               allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0))
        control.lock_times = [pc_control.dtime(20, 0)]

        reached, reason = control.check_time_limits(
            datetime(2026, 7, 30, 20, 0, 30))

        self.assertTrue(reached)
        self.assertIn('Scheduled lock', reason)

    def test_exempt_user_is_not_limited_by_the_window(self):
        night = datetime(2026, 7, 30, 3, 0, 0)
        control = make_control(night, current_user='ParentAdmin',
                               allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0))

        with patch.object(pc_control, 'EXEMPT_USERS', ['ParentAdmin']):
            self.assertFalse(control.check_time_limits(night)[0])

    def test_set_and_clear_window_are_persisted(self):
        control = make_control(datetime(2026, 7, 30, 12, 0, 0))

        control.set_allowed_window(pc_control.dtime(8, 30), pc_control.dtime(19, 45))

        self.assertEqual(control.get_allowed_window(),
                         (pc_control.dtime(8, 30), pc_control.dtime(19, 45)))
        control.save_state.assert_called_once_with()

        control.clear_allowed_window()
        self.assertEqual(control.get_allowed_window(), (None, None))


class TemporaryUnlockTests(unittest.TestCase):
    """A parent-granted unlock is bonus time: it wins over window and budget."""

    def _walled_control(self, now, **kwargs):
        control = make_control(now, allowed_start=pc_control.dtime(7, 0),
                               allowed_end=pc_control.dtime(22, 0), **kwargs)
        return control

    def test_unlock_allows_use_outside_the_window(self):
        night = datetime(2026, 7, 30, 23, 0, 0)
        control = self._walled_control(night, usage_limit=240)
        control.unlock_until = night + timedelta(minutes=30)

        self.assertFalse(control.check_time_limits(night)[0])

    def test_unlock_overrides_an_exhausted_daily_limit(self):
        night = datetime(2026, 7, 30, 23, 0, 0)
        control = self._walled_control(night, usage_limit=120,
                                       accrued_seconds=130 * 60)
        control.unlock_until = night + timedelta(minutes=30)

        reached, reason = control.check_time_limits(night)

        self.assertFalse(reached)
        self.assertEqual(reason, '')

    def test_unlock_expires_and_the_window_applies_again(self):
        night = datetime(2026, 7, 30, 23, 0, 0)
        control = self._walled_control(night, usage_limit=240)
        control.unlock_until = night + timedelta(minutes=30)

        later = night + timedelta(minutes=45)

        self.assertTrue(control.check_time_limits(later)[0])
        self.assertIsNone(control.unlock_until)
        control.save_state.assert_called()

    def test_bonus_time_is_not_charged_to_the_daily_budget(self):
        night = datetime(2026, 7, 30, 23, 0, 0)
        control = self._walled_control(night, usage_limit=120)
        # _should_accrue_time() reads the wall clock, so the grant must be live.
        control.unlock_until = datetime.now() + timedelta(minutes=30)

        self.assertFalse(control._should_accrue_time())

    def test_accrual_resumes_after_the_unlock_expires(self):
        night = datetime(2026, 7, 30, 23, 0, 0)
        control = self._walled_control(night, usage_limit=120)
        control.unlock_until = night - timedelta(minutes=1)

        self.assertTrue(control._should_accrue_time())

    def test_scheduled_lock_time_still_fires_during_an_unlock(self):
        control = self._walled_control(datetime(2026, 7, 30, 20, 0, 0),
                                       usage_limit=240)
        control.lock_times = [pc_control.dtime(20, 0)]
        control.unlock_until = datetime(2026, 7, 30, 20, 30, 0)

        reached, reason = control.check_time_limits(
            datetime(2026, 7, 30, 20, 0, 10))

        self.assertTrue(reached)
        self.assertIn('Scheduled lock', reason)

    def test_no_warning_fires_while_an_unlock_is_running(self):
        now = datetime(2026, 7, 30, 23, 0, 0)
        control = self._walled_control(now, usage_limit=120,
                                       accrued_seconds=130 * 60)
        control.unlock_until = now + timedelta(minutes=30)
        control.show_message = Mock()

        control.check_and_send_warnings()

        control.show_message.assert_not_called()

    def test_cancel_unlock_revokes_the_grant(self):
        control = make_control(datetime(2026, 7, 30, 23, 0, 0))
        control.unlock_until = datetime.now() + timedelta(minutes=30)

        self.assertTrue(control.cancel_unlock())
        self.assertIsNone(control.unlock_until)
        self.assertFalse(control.cancel_unlock())


class WindowAndUnlockStateTests(unittest.TestCase):
    def _control_for(self, state_file):
        # Real save_state here: these tests exercise the file round trip.
        control = make_control(datetime.now())
        control.state_file = str(state_file)
        control.logger = Mock()
        control.save_state = lambda: pc_control.PCTimeControl.save_state(control)
        return control

    def test_state_without_a_window_means_window_off(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'state.json'
            state_file.write_text(json.dumps({
                'usage_limit': 120,
                'accrued_seconds': 0,
                'start_time': datetime.now().isoformat(),
            }))
            control = self._control_for(state_file)
            control.load_state()

        self.assertIsNone(control.allowed_start)
        self.assertIsNone(control.allowed_end)
        self.assertTrue(control.is_within_allowed_window(datetime.now()))

    def test_state_round_trips_the_window(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'state.json'
            control = self._control_for(state_file)
            control.allowed_start = pc_control.dtime(6, 15)
            control.allowed_end = pc_control.dtime(21, 45)

            control.save_state()

            reloaded = self._control_for(state_file)
            reloaded.load_state()

        self.assertEqual(reloaded.get_allowed_window(),
                         (pc_control.dtime(6, 15), pc_control.dtime(21, 45)))

    def test_malformed_window_is_ignored_not_fatal(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'state.json'
            state_file.write_text(json.dumps({
                'allowed_start': 'seven', 'allowed_end': '22:00',
                'start_time': datetime.now().isoformat(),
            }))
            control = self._control_for(state_file)
            control.load_state()

        self.assertIsNone(control.allowed_start)
        control.logger.error.assert_called()

    def test_running_unlock_survives_a_restart(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'state.json'
            until = datetime.now() + timedelta(minutes=20)
            state_file.write_text(json.dumps({
                'unlock_until': until.isoformat(),
                'start_time': datetime.now().isoformat(),
            }))
            control = self._control_for(state_file)
            control.load_state()

        self.assertIsNotNone(control.unlock_until)
        self.assertTrue(control.is_unlock_active())

    def test_expired_unlock_is_not_resurrected_by_a_restart(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'state.json'
            until = datetime.now() - timedelta(minutes=20)
            state_file.write_text(json.dumps({
                'unlock_until': until.isoformat(),
                'start_time': datetime.now().isoformat(),
            }))
            control = self._control_for(state_file)
            control.load_state()

        self.assertIsNone(control.unlock_until)
        self.assertFalse(control.is_unlock_active())


class ConsoleUserCacheTests(unittest.TestCase):
    """The console-user lookup costs a PowerShell launch; it must be cached."""

    def test_lookup_runs_once_within_the_refresh_interval(self):
        control = make_control(datetime.now(), current_user='Kids')
        control._get_console_user = Mock(return_value='Kids')
        control.last_user_refresh = 0.0

        for _ in range(5):
            control.should_monitor_user()

        self.assertEqual(control._get_console_user.call_count, 1)

    def test_force_refresh_bypasses_the_cache(self):
        control = make_control(datetime.now(), current_user='Kids')
        control._get_console_user = Mock(return_value='Kids')
        control.last_user_refresh = pc_control.time.monotonic()

        control.refresh_current_user()
        self.assertEqual(control._get_console_user.call_count, 0)

        control.refresh_current_user(force=True)
        self.assertEqual(control._get_console_user.call_count, 1)

    def test_a_new_console_user_is_picked_up(self):
        control = make_control(datetime.now(), current_user='Kids')
        control._get_console_user = Mock(return_value='ParentAdmin')
        control.last_user_refresh = 0.0

        control.refresh_current_user()

        self.assertEqual(control.current_user, 'ParentAdmin')


class ImmediateThread:
    """Run the thread body inline so message dispatch is testable."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self.target = target

    def start(self):
        self.target()


class MessageDeliveryTests(unittest.TestCase):
    """msg.exe is missing on Windows Home, so tkinter must be the fallback."""

    def setUp(self):
        self.control = make_control(datetime.now(), current_user='Kids')
        self.control._show_via_tk = Mock()

    def test_msg_exe_is_preferred_when_it_works(self):
        completed = Mock(returncode=0, stderr=b'')
        with patch.object(pc_control, 'threading') as fake_threading, \
             patch.object(pc_control.subprocess, 'run', return_value=completed) as run:
            fake_threading.Thread = ImmediateThread
            self.control.show_message('Time is up')

        self.assertEqual(run.call_args[0][0][0], 'msg')
        self.control._show_via_tk.assert_not_called()

    def test_missing_msg_exe_falls_back_to_tkinter(self):
        with patch.object(pc_control, 'threading') as fake_threading, \
             patch.object(pc_control.subprocess, 'run',
                          side_effect=FileNotFoundError('msg')):
            fake_threading.Thread = ImmediateThread
            self.control.show_message('Time is up')

        self.control._show_via_tk.assert_called_once()

    def test_failing_msg_exe_falls_back_to_tkinter(self):
        completed = Mock(returncode=1, stderr=b'recipient not found')
        with patch.object(pc_control, 'threading') as fake_threading, \
             patch.object(pc_control.subprocess, 'run', return_value=completed):
            fake_threading.Thread = ImmediateThread
            self.control.show_message('Time is up')

        self.control._show_via_tk.assert_called_once()


class LockActionTests(unittest.TestCase):
    def test_default_lock_method_uses_lock_work_station(self):
        control = make_control(datetime.now())
        with patch.object(pc_control, 'LOCK_METHOD', 'lock'), \
             patch.object(pc_control.ctypes, 'windll', create=True) as windll:
            control.lock_pc()

        windll.user32.LockWorkStation.assert_called_once()
        self.assertTrue(control.is_locked)

    def test_opt_in_logoff_method_signs_the_console_session_off(self):
        control = make_control(datetime.now())
        query = Mock(stdout=' SESSIONNAME  USERNAME  ID  STATE\n'
                            ' console      Kids      1   Active\n', returncode=0)
        logoff = Mock(returncode=0)
        with patch.object(pc_control, 'LOCK_METHOD', 'logoff'), \
             patch.object(pc_control.subprocess, 'run', side_effect=[query, logoff]) as run:
            control.lock_pc()

        self.assertIn('query session', run.call_args_list[0][0][0])
        self.assertEqual(run.call_args_list[1][0][0], ['logoff', '1'])
        self.assertTrue(control.is_locked)

if __name__ == '__main__':
    unittest.main()
