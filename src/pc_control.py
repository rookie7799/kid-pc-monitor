import os
import sys
import time
import datetime
import ctypes
import socket
import threading
try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:  # Headless/Linux test runs: the agent logic must still import.
    tk = None
    messagebox = None
from datetime import datetime, time as dtime, timedelta
import subprocess
from ctypes import wintypes
import getpass
import json
import shutil

import logging
from pathlib import Path
from warning_logic import (
    warnings_to_send,
    initial_remaining_notice,
    format_remaining_message,
)

# ============================================
# CONFIGURATION
# ============================================

# List of Windows usernames to monitor (leave empty to monitor all users)
# Example: MONITORED_USERS = ['Tommy', 'Sarah', 'kid1']
MONITORED_USERS = []

# List of Windows usernames to EXEMPT from monitoring (parents/admins)
# Example: EXEMPT_USERS = ['pavel', 'Mom', 'Dad', 'Administrator']
EXEMPT_USERS = []

# If both lists are empty, ALL users will be monitored
# If MONITORED_USERS has entries, ONLY those users are monitored
# If EXEMPT_USERS has entries, everyone EXCEPT those users is monitored

# Lock action. LockWorkStation() locks the console session the agent runs in,
# which is how the installer starts it (inside the kid's own session). A
# deployment that runs the agent as SYSTEM / Session 0 cannot lock another
# session that way; set this to 'logoff' to sign the console session off
# instead. Logging off closes every open program, so it is opt-in only.
LOCK_METHOD = 'lock'  # 'lock' | 'logoff'


def _format_clock(value):
    """Return HH:MM for a time-of-day, or None when the value is unset."""
    return f"{value.hour:02d}:{value.minute:02d}" if value else None


# ============================================

DATA_DIR_ENV = 'KID_PC_MONITOR_DATA_DIR'
DATA_DIR_NAME = 'KidPCMonitor'


def resolve_data_dir(environ=None):
    """Return a writable per-user directory for agent state and logs.

    Installed code can live under Program Files, where a standard child account
    correctly has no write access. Windows exposes LOCALAPPDATA for the account
    that owns the interactive scheduled task, so mutable files belong there.
    The explicit override is useful for tests and controlled deployments. On
    non-Windows systems we preserve the historical current-directory behavior.
    """
    environ = os.environ if environ is None else environ
    override = environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()

    local_appdata = environ.get('LOCALAPPDATA')
    if local_appdata:
        return Path(local_appdata) / DATA_DIR_NAME

    return Path.cwd()


STATE_FILE_NAME = 'pc_control_state.json'


def migrate_legacy_state_file(target, legacy_dirs=None):
    """Copy a pre-DATA_DIR state file once, so an upgrade keeps its settings.

    Before the move to %LOCALAPPDATA%, the state lived next to the code, so an
    upgrade that only changes the path would silently start from an empty state
    (no usage limit, no lock times, no window). The scheduled task sets its
    working directory to the script's folder, so both that folder and the
    current directory are checked. Returns the source path when a file was
    copied, otherwise None.
    """
    target = Path(target)
    if target.exists():
        return None

    if legacy_dirs is None:
        legacy_dirs = [Path(__file__).resolve().parent, Path.cwd()]

    for legacy_dir in legacy_dirs:
        legacy_file = Path(legacy_dir) / STATE_FILE_NAME
        try:
            if not legacy_file.is_file():
                continue
            if legacy_file.resolve() == target.resolve():
                continue
        except OSError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_file, target)
        return legacy_file
    return None



DATA_DIR = resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Set up logging
log_file = DATA_DIR / 'pc_control.log'
if os.path.exists(log_file):
    os.unlink(log_file) #remove previous log

logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# When launched via pythonw.exe (as the scheduled task does) there is no
# console, so sys.stdout/sys.stderr are None and the first print() raises
# AttributeError, killing the agent before the server can bind port 9999.
# Redirect them to a file so print() is safe and its output is captured.
if sys.stdout is None or sys.stderr is None:
    _console_log = open(DATA_DIR / 'pc_control.out.log', 'a', buffering=1, encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = _console_log
    if sys.stderr is None:
        sys.stderr = _console_log

class PCTimeControl:
    # Maximum active usage that may be lost on an unclean shutdown: the state
    # file is rewritten every SAVE_INTERVAL_SECONDS of accrued usage.
    SAVE_INTERVAL_SECONDS = 30
    # A polling step longer than this means the machine slept, hibernated, or
    # the loop stalled: that gap is not usage and must not be charged.
    MAX_SAMPLE_GAP_SECONDS = 15
    # The console-user lookup spawns PowerShell (~0.3 s), so it is cached and
    # refreshed on this interval instead of on every loop iteration.
    USER_REFRESH_SECONDS = 45

    def _get_console_user(self):
        """Return the username of the active console session, or None.

        Uses PowerShell to avoid locale-dependent parsing of ``query user``.
        """
        try:
            out = subprocess.check_output(
                [
                    'powershell', '-NoProfile', '-Command',
                    '(Get-CimInstance -ClassName Win32_ComputerSystem).UserName'
                ],
                text=True, timeout=10,
            )
            username = out.strip()
            if username and '\\' in username:
                return username.split('\\', 1)[1]
            return username or None
        except Exception:
            return None

    def __init__(self):
        self.lock_times = []
        self.usage_limit = None
        self.start_time = datetime.now()
        # Active (unlocked) usage accrued for the current day. The daily limit
        # is a budget of *used* time, not wall-clock time since start_time: a
        # locked screen or an exempt parent must not burn the child's budget.
        self.accrued_seconds = 0.0
        # accrued_seconds as last written to disk, so the monitor persists on a
        # fixed schedule instead of guessing with a modulo check.
        self.last_saved_accrued = 0.0
        self.is_locked = False
        self.last_activity = datetime.now()
        self.current_user = self._get_console_user() or getpass.getuser()
        self.last_user_refresh = time.monotonic()
        self.state_file = DATA_DIR / STATE_FILE_NAME
        self.logger = logging.getLogger('PCTimeControl')
        # Upgrades from the build that kept the state next to the code would
        # otherwise start with no limit, no lock times and no window.
        migrated_from = migrate_legacy_state_file(self.state_file)
        if migrated_from:
            self.logger.info(
                "Migrated previous settings from %s to %s", migrated_from, self.state_file
            )
            print(f"[{datetime.now():%H:%M:%S}] Migrated settings from {migrated_from}")
        self.warnings_sent = set()  # Threshold minutes already announced this episode
        self.warning_intervals = [15, 5, 1]  # Warning times in minutes before lock
        # Guards the limit/start_time/warnings_sent state that the server thread
        # mutates while the monitor thread reads it (avoids locking against a
        # half-applied limit change).
        self.state_lock = threading.RLock()
        self.last_remaining = None  # Previous remaining minutes, for crossing detection
        # Allowed-usage window. None = never enforced, which is the default: a
        # window must be opted into by a parent, otherwise every existing
        # install would start locking outside business hours after upgrading.
        self.allowed_start = None
        self.allowed_end = None
        # Temporary override: the PC stays usable until this moment even outside
        # the window and with the daily budget spent. None = no override.
        self.unlock_until = None

        # Log which user we're running as
        if self.should_monitor_user():
            self.logger.info(f"Monitoring enabled for user: {self.current_user}")
            print(f"[{datetime.now():%H:%M:%S}] Monitoring user: {self.current_user}")
        else:
            self.logger.info(f"User {self.current_user} is EXEMPT from monitoring")
            print(f"[{datetime.now():%H:%M:%S}] User {self.current_user} is EXEMPT - no restrictions will apply")

        # Load previous state if exists
        self.load_state()

        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self.monitor_activity, daemon=True)
        self.monitor_thread.start()

    def refresh_current_user(self, force=False):
        """Update current_user from the active console session.

        Throttled to USER_REFRESH_SECONDS: the lookup spawns PowerShell, and
        without the cache it fired from every caller of should_monitor_user()
        (the warning check, the lock check and the accrual tick) — a handful of
        process launches per second.
        """
        now = time.monotonic()
        if not force and (now - self.last_user_refresh) < self.USER_REFRESH_SECONDS:
            return
        self.last_user_refresh = now
        console_user = self._get_console_user()
        if console_user and console_user != self.current_user:
            self.logger.info(
                "User changed: %s -> %s", self.current_user, console_user
            )
            self.current_user = console_user

    def should_monitor_user(self):
        """Check if current user should be monitored based on configuration"""
        self.refresh_current_user()
        # If MONITORED_USERS is specified, only monitor those users
        if MONITORED_USERS:
            return self.current_user in MONITORED_USERS

        # If EXEMPT_USERS is specified, monitor everyone except those users
        if EXEMPT_USERS:
            return self.current_user not in EXEMPT_USERS

        # If both lists are empty, monitor all users
        return True

    def load_state(self):
        """Load saved state from JSON file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)

                # Restore lock times
                if 'lock_times' in state:
                    self.lock_times = [dtime(*map(int, t.split(':'))) for t in state['lock_times']]

                # Restore usage limit
                if 'usage_limit' in state:
                    self.usage_limit = state['usage_limit']

                # Restore accrued active usage (0 for state files written
                # before usage was tracked independently of start_time).
                legacy_usage = 'accrued_seconds' not in state
                self.accrued_seconds = float(state.get('accrued_seconds') or 0.0)
                # The value just read is, by definition, what is on disk.
                self.last_saved_accrued = self.accrued_seconds

                # Restore the allowed-usage window. Old state files have none,
                # which means "not enforced" — the same default as a new install.
                if state.get('allowed_start') and state.get('allowed_end'):
                    try:
                        self.allowed_start = dtime(*map(int, state['allowed_start'].split(':')))
                        self.allowed_end = dtime(*map(int, state['allowed_end'].split(':')))
                    except (ValueError, TypeError):
                        self.logger.error("Ignoring malformed allowed window in state")
                        self.allowed_start = None
                        self.allowed_end = None

                # Restore a temporary unlock only while it is still running: an
                # expired grant must not be resurrected by a restart.
                if state.get('unlock_until'):
                    try:
                        until = datetime.fromisoformat(state['unlock_until'])
                        if until > datetime.now():
                            self.unlock_until = until
                    except (ValueError, TypeError):
                        pass

                # Restore start time (for usage tracking)
                if 'start_time' in state:
                    saved_start_time = datetime.fromisoformat(state['start_time'])
                    current_date = datetime.now().date()
                    saved_date = saved_start_time.date()

                    # A usage limit is daily. Never carry elapsed time across a
                    # date boundary (and recover safely from a future date left
                    # by a clock correction). Both the start_time AND the
                    # accrued usage reset — otherwise yesterday's exhausted
                    # limit "sticks" to the new day and the PC shows 0 minutes
                    # remaining right after boot.
                    if saved_date != current_date:
                        self.start_time = datetime.now()
                        self.accrued_seconds = 0.0
                        self.last_saved_accrued = 0.0
                        self.logger.info(f"Start time was from {saved_date}, reset to today")
                        print(f"[{datetime.now():%H:%M:%S}] Usage timer reset for new day")
                    else:
                        self.start_time = saved_start_time
                        if legacy_usage:
                            # Usage used to be derived from the wall-clock
                            # distance since start_time. Convert the same-day
                            # elapsed time once, so upgrading mid-day does not
                            # hand the child a fresh budget.
                            self.accrued_seconds = max(
                                0.0,
                                (datetime.now() - saved_start_time).total_seconds(),
                            )
                            self.logger.info(
                                "Migrated legacy state: %.0f s of usage today",
                                self.accrued_seconds,
                            )

                self.logger.info(f"State loaded: {len(self.lock_times)} lock times, usage limit: {self.usage_limit}")
                print(f"[{datetime.now():%H:%M:%S}] Loaded previous settings from {self.state_file}")
        except Exception as e:
            self.logger.error(f"Error loading state: {e}")
            print(f"[{datetime.now():%H:%M:%S}] Could not load previous state: {e}")

    def save_state(self):
        """Save current state to JSON file"""
        try:
            state = {
                'lock_times': [f"{lt.hour}:{lt.minute}" for lt in self.lock_times],
                'usage_limit': self.usage_limit,
                'accrued_seconds': round(self.accrued_seconds, 1),
                'allowed_start': _format_clock(self.allowed_start),
                'allowed_end': _format_clock(self.allowed_end),
                'unlock_until': self.unlock_until.isoformat() if self.unlock_until else None,
                'start_time': self.start_time.isoformat(),
                'current_user': self.current_user
            }

            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)

            # Remember what went to disk so the monitor persists on accumulated
            # usage instead of re-saving at arbitrary moments.
            self.last_saved_accrued = self.accrued_seconds

            self.logger.info("State saved successfully")
        except Exception as e:
            self.logger.error(f"Error saving state: {e}")

    def check_if_locked(self):
        """
        Returns True if LogonUI.exe is present (screen locked),
        False otherwise.
        """
        try:
            out = subprocess.check_output(
                'tasklist /FI "IMAGENAME eq LogonUI.exe" /NH',
                shell=True,
                text=True
            )
            locked = "LogonUI.exe" in out
            # print(f"[{datetime.now():%H:%M:%S}] LogonUI.exe running? {locked}")
            return locked
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error checking LogonUI: {e}")
            # fallback to whatever you had before (or assume unlocked)
            return False

    def monitor_activity(self):
        """Monitor lock/unlock status and accrue active (unlocked) usage time."""
        last = time.monotonic()
        last_date = datetime.now().date()
        while True:
            actual_locked = self.check_if_locked()

            # Detect unlock
            if self.is_locked and not actual_locked:
                self.is_locked = False
                print(f"[{datetime.now().strftime('%H:%M:%S')}] PC has been unlocked (detected by activity)")

            # Detect manual lock (not by our script)
            elif not self.is_locked and actual_locked:
                self.is_locked = True
                print(f"[{datetime.now().strftime('%H:%M:%S')}] PC has been locked (detected)")

            # Accrue active usage only while the PC is unlocked and the console
            # user is monitored: neither a lock screen nor a parent's own
            # session may consume the child's daily budget.
            now = time.monotonic()
            current_time = datetime.now()
            delta = self._active_sample_seconds(now - last, last_date,
                                                current_time.date())
            last = now
            last_date = current_time.date()
            if self._should_accrue_time():
                self.reset_daily_usage_if_needed(current_time)
                self._accrue_usage(delta)

            time.sleep(3)  # Check every 3 seconds

    @classmethod
    def _active_sample_seconds(cls, elapsed, previous_date, current_date):
        """Return the seconds of this poll that count as usage.

        A gap across midnight belongs to neither day and a gap larger than
        MAX_SAMPLE_GAP_SECONDS is sleep/hibernate/stall, not screen time.
        """
        if previous_date != current_date or elapsed > cls.MAX_SAMPLE_GAP_SECONDS:
            return 0.0
        return max(0.0, elapsed)

    def _should_accrue_time(self):
        """Whether this poll's time should count against the daily limit.

        Bonus time from a temporary unlock is deliberately free: the parent
        granted minutes of screen time, not a debit against tomorrow's budget.
        """
        if self.is_locked or self.is_unlock_active():
            return False
        return self.should_monitor_user()

    def _accrue_usage(self, delta_seconds):
        """Add active usage time and persist it on a predictable schedule.

        The state file is rewritten every SAVE_INTERVAL_SECONDS of *accumulated*
        usage, so an unclean shutdown loses at most that much. Tracking the
        delta against the last save is deliberate: a modulo check on the running
        total lands on the interval only by luck, so it saved erratically.
        """
        if self.usage_limit is None:
            return
        with self.state_lock:
            self.accrued_seconds += delta_seconds
        if self.accrued_seconds - self.last_saved_accrued >= self.SAVE_INTERVAL_SECONDS:
            # Mark the value flushed before writing so a failed write does not
            # turn the next tick into an immediate retry loop.
            self.last_saved_accrued = self.accrued_seconds
            self.save_state()

    def reset_daily_usage_if_needed(self, current_time=None):
        """Start a fresh daily budget when the calendar day changes.

        Returns True when a reset happened. Without this the agent only reset on
        startup, so a PC left running overnight kept yesterday's exhausted
        counter until the next reboot.
        """
        current_time = current_time or datetime.now()
        with self.state_lock:
            if self.start_time.date() == current_time.date():
                return False
            previous_date = self.start_time.date()
            self.start_time = current_time
            self.accrued_seconds = 0.0
            self.last_saved_accrued = 0.0
            self.warnings_sent.clear()
            self.last_remaining = None  # Re-arm crossing detection
        self.save_state()
        self.logger.info(
            "Usage timer reset for new day (previous start date: %s)",
            previous_date,
        )
        return True

    def add_scheduled_lock(self, hour, minute):
        """Add a time when the PC should be locked"""
        self.lock_times.append(dtime(hour, minute))

    def set_usage_limit(self, minutes):
        """Set maximum usage time in minutes"""
        self.usage_limit = minutes

    def show_message(self, message, title="PC Time Control"):
        def _show_via_msg():
            try:
                user = self.current_user or getpass.getuser()
                result = subprocess.run(
                    ['msg', user, '/TIME:60', message],
                    capture_output=True, timeout=65,
                )
                if result.returncode == 0:
                    self.logger.info("Message sent to %s via msg.exe", user)
                    return True
                self.logger.warning(
                    "msg.exe exited %s: %s", result.returncode, result.stderr
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.logger.warning("msg.exe unavailable: %s", exc)
            return False

        def _notify():
            if not _show_via_msg():
                self._show_via_tk(message, title)

        threading.Thread(target=_notify, daemon=True).start()

    def _show_via_tk(self, message, title):
        """Fallback message box for interactive desktop sessions."""
        if tk is None:
            self.logger.error("Cannot show message (no msg.exe, no Tkinter): %s", message)
            return
        root = None
        try:
            root = tk.Tk()
            root.withdraw()  # Hide the main window
            root.attributes('-topmost', True)  # Make it appear on top
            # Auto-close after 60 seconds to prevent hanging
            root.after(60000, root.destroy)
            messagebox.showwarning(title, message)
        except Exception as e:
            self.logger.error(f"Error showing message via Tkinter: {e}")
            print(f"[{datetime.now():%H:%M:%S}] Error showing message: {e}")
        finally:
            if root:
                try:
                    root.quit()
                    root.destroy()
                except Exception:
                    pass  # Already destroyed

    def lock_pc(self):
        """Lock the Windows PC (active console session).

        LockWorkStation() is the default: the installer starts the agent inside
        the kid's own session, where it locks exactly that session. Deployments
        that run the agent as SYSTEM / Session 0 must opt into LOCK_METHOD =
        'logoff', which signs the console session off instead.
        """
        try:
            self.is_locked = True
            if LOCK_METHOD == 'logoff':
                self._logoff_console_session()
            else:
                ctypes.windll.user32.LockWorkStation()
                self.logger.info("PC locked successfully")
        except Exception as e:
            self.logger.error(f"Error locking PC: {e}")
            print(f"[{datetime.now():%H:%M:%S}] Error locking PC: {e}")

    def _logoff_console_session(self):
        """Sign the console session off (opt-in LOCK_METHOD='logoff').

        Needed when the agent runs as SYSTEM: LockWorkStation() from Session 0
        locks the wrong session. ``query session`` is parsed for the session id
        on the line carrying "console" (an English word in every locale).
        """
        result = subprocess.run(
            'query session', shell=True,
            capture_output=True, text=True, timeout=10,
        )
        session_id = None
        for line in result.stdout.splitlines():
            if 'console' in line.lower():
                for part in line.split():
                    if part.isdigit():
                        session_id = part
                        break
                break
        if not session_id:
            self.logger.error("No console session found; falling back to LockWorkStation")
            ctypes.windll.user32.LockWorkStation()
            return
        result = subprocess.run(
            ['logoff', session_id], capture_output=True, timeout=10,
        )
        if result.returncode:
            self.logger.error(
                "Logoff failed (session %s, exit code %s)", session_id, result.returncode
            )
        else:
            self.logger.info("Console session %s logged off", session_id)

    def shutdown_pc(self, seconds=60):
        """Shutdown PC with warning"""
        try:
            os.system(f'shutdown /s /t {seconds} /c "Computer will shutdown in {seconds} seconds"')
            self.logger.info(f"Shutdown initiated ({seconds}s)")
        except Exception as e:
            self.logger.error(f"Error initiating shutdown: {e}")
            print(f"[{datetime.now():%H:%M:%S}] Error shutting down: {e}")

    def cancel_shutdown(self):
        """Cancel pending shutdown"""
        os.system('shutdown /a')

    def set_allowed_window(self, start, end):
        """Set the allowed-usage window (start/end as datetime.time)."""
        with self.state_lock:
            self.allowed_start = start
            self.allowed_end = end
        self.save_state()
        self.logger.info(
            "Allowed window set to %s - %s", _format_clock(start), _format_clock(end)
        )

    def clear_allowed_window(self):
        """Stop enforcing an availability window."""
        with self.state_lock:
            self.allowed_start = None
            self.allowed_end = None
        self.save_state()
        self.logger.info("Allowed window disabled")

    def get_allowed_window(self):
        """Return (start, end) as time objects, or (None, None) when disabled."""
        with self.state_lock:
            return self.allowed_start, self.allowed_end

    def is_within_allowed_window(self, current_time=None):
        """Whether the PC may be used right now.

        No configured window means "always allowed". A window that ends before
        it starts crosses midnight (22:00-07:00), so the early morning is
        inside it and the evening after the start time is inside it too.
        """
        start, end = self.get_allowed_window()
        if start is None or end is None:
            return True
        current_time = current_time or datetime.now()
        if self.is_unlock_active(current_time):
            return True
        now = dtime(current_time.hour, current_time.minute)
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    def is_unlock_active(self, current_time=None):
        """Whether a parent-granted temporary unlock is still running."""
        with self.state_lock:
            until = self.unlock_until
        if not until:
            return False
        current_time = current_time or datetime.now()
        if current_time < until:
            return True
        # Expired: drop it so the normal rules apply again (and it is not
        # resurrected by a restart).
        with self.state_lock:
            if self.unlock_until == until:
                self.unlock_until = None
        self.save_state()
        self.logger.info("Temporary unlock expired")
        return False

    def unlock_for(self, minutes):
        """Grant exactly `minutes` of parent-approved bonus screen time.

        The grant overrides both the allowed window and an exhausted daily
        limit — it is an explicit parent decision, e.g. "finish your homework".
        Accrual is paused meanwhile (see _should_accrue_time), so bonus time is
        not charged to the daily budget.
        """
        with self.state_lock:
            self.unlock_until = datetime.now() + timedelta(minutes=minutes)
            until = self.unlock_until
        self.save_state()
        self.logger.info("Temporary unlock granted until %s", until)
        return until

    def cancel_unlock(self):
        """Revoke an active temporary unlock. Returns True if one was running."""
        with self.state_lock:
            was_active = self.unlock_until is not None
            self.unlock_until = None
        if was_active:
            self.save_state()
            self.logger.info("Temporary unlock cancelled")
        return was_active

    def get_time_remaining(self, current_time=None):
        """Calculate minutes remaining until lock. Returns None if no limit set."""
        if not self.should_monitor_user():
            return None

        current_time = current_time or datetime.now()
        self.reset_daily_usage_if_needed(current_time)
        min_remaining = None

        with self.state_lock:
            lock_times = list(self.lock_times)
            usage_limit = self.usage_limit
            usage_minutes = self.accrued_seconds / 60.0

        # Check scheduled lock times
        for lock_time in lock_times:
            lock_datetime = current_time.replace(hour=lock_time.hour, minute=lock_time.minute, second=0, microsecond=0)

            # If lock time is earlier today, it's for tomorrow. Use timedelta
            # so month/year roll over correctly (replace(day=day+1) raises
            # ValueError on the last day of a month).
            if lock_datetime <= current_time:
                lock_datetime = lock_datetime + timedelta(days=1)

            minutes_until_lock = (lock_datetime - current_time).total_seconds() / 60

            if min_remaining is None or minutes_until_lock < min_remaining:
                min_remaining = minutes_until_lock

        # Check usage limit. Use `is not None` so a limit of 0 (lock immediately)
        # is honoured rather than treated as "no limit" by a falsy check.
        if usage_limit is not None:
            minutes_until_limit = usage_limit - usage_minutes

            if min_remaining is None or minutes_until_limit < min_remaining:
                min_remaining = minutes_until_limit

        # Never report negative time; once the limit is reached it's simply 0.
        if min_remaining is not None and min_remaining < 0:
            min_remaining = 0

        return min_remaining

    def check_and_send_warnings(self):
        """Check if warnings should be sent and send them.

        Delegates the crossing decision to warning_logic.warnings_to_send: a
        warning fires only when the remaining time crosses down through a
        threshold (the previous tick was above it, this tick is at or below it).
        This avoids over-stating the time: if the kid is granted only 10
        minutes, the 15-minute warning never fires because the time never
        actually started above 15. last_remaining is reset to None whenever the
        limit changes so the next limit re-arms crossing from a clean slate.
        """
        time_remaining = self.get_time_remaining()

        if time_remaining is None:
            self.last_remaining = None
            return

        # While a temporary unlock is running nothing is counting down, so a
        # "0 minutes left" warning would be wrong.
        if self.is_unlock_active():
            self.last_remaining = time_remaining
            return

        previous = self.last_remaining
        self.last_remaining = time_remaining

        # On the first reading after a (re)set, tell a late-logging-in kid how
        # much time is actually left instead of staying silent until the next
        # interval is crossed.
        notice = initial_remaining_notice(previous, time_remaining,
                                          self.warning_intervals)
        if notice is not None:
            msg = format_remaining_message(notice)
            self.show_message(msg, "Warning")
            self.logger.info(f"Initial time notice: {notice:.2f} minutes remaining")
            print(f"[{datetime.now():%H:%M:%S}] {msg}")

        for warning_mins in warnings_to_send(previous, time_remaining,
                                              self.warning_intervals,
                                              self.warnings_sent):
            self.warnings_sent.add(warning_mins)

            if warning_mins == 1:
                msg = "⚠️ Computer will lock in 1 minute!"
            else:
                msg = f"⚠️ Computer will lock in {warning_mins} minutes!"

            self.show_message(msg, "Warning")
            self.logger.info(f"Warning sent: {warning_mins} minutes remaining")
            print(f"[{datetime.now():%H:%M:%S}] Warning: {warning_mins} minutes until lock")

    def check_time_limits(self, current_time=None):
        """Check if any time limits have been reached"""
        # Skip all checks if user is exempt from monitoring
        if not self.should_monitor_user():
            return False, ""

        current_time = current_time or datetime.now()
        self.reset_daily_usage_if_needed(current_time)

        # A temporary unlock is an explicit parent decision to let the PC be
        # used now, so it wins over both the window and an exhausted limit.
        # Everything else (scheduled lock times) still applies: those are
        # bedtime, not a budget.
        if self.is_unlock_active(current_time):
            with self.state_lock:
                lock_times = list(self.lock_times)
            for lock_time in lock_times:
                if (current_time.hour == lock_time.hour and
                        current_time.minute == lock_time.minute):
                    return True, "Scheduled lock time reached"
            return False, ""

        with self.state_lock:
            lock_times = list(self.lock_times)
            usage_limit = self.usage_limit
            usage_minutes = self.accrued_seconds / 60.0

        # Check scheduled lock times. Match the whole target minute, not just
        # its first second: the monitor loop does work + sleep(1) each pass and
        # can step straight over second 0, missing the lock entirely.
        for lock_time in lock_times:
            if (current_time.hour == lock_time.hour and
                current_time.minute == lock_time.minute):
                return True, "Scheduled lock time reached"

        # Check usage limit. Use `is not None` so a limit of 0 (lock immediately)
        # is honoured rather than treated as "no limit" by a falsy check.
        if usage_limit is not None:
            if usage_minutes >= usage_limit:
                return True, f"Usage limit of {usage_limit} minutes reached"

        # Outside the configured availability window the PC is locked again on
        # re-entry. No window configured means this never triggers.
        if not self.is_within_allowed_window(current_time):
            return True, "Outside allowed usage window"

        return False, ""

    def run_monitor(self):
        """Main monitoring loop: enforces limits for the whole session.

        Runs forever — it must NOT exit after the first lock, otherwise the kid
        could unlock the screen and use the PC unrestricted for the rest of the
        day. Locking only happens on the unlocked->over-limit transition so we
        don't re-issue LockWorkStation every second while already at the lock
        screen; monitor_activity clears is_locked when the screen is unlocked,
        which re-arms enforcement if the limit is still exceeded.
        """
        print("PC Time Control is running...")
        while True:
            try:
                # Check and send warnings if approaching time limit
                self.check_and_send_warnings()

                # Check if time limit reached
                should_lock, reason = self.check_time_limits()
                if should_lock and not self.is_locked:
                    print(f"Locking PC: {reason}")
                    self.lock_pc()
            except Exception as e:
                # Never let a transient error kill the enforcement loop.
                self.logger.error(f"Error in monitor loop: {e}")
                print(f"[{datetime.now():%H:%M:%S}] Monitor loop error: {e}")
            time.sleep(1)

# Simple Remote Control Server
class RemoteControlServer:
    def __init__(self, port=9999, timeout=60):
        """
        Initialize the remote control server.
        
        Args:
            port (int): Port number to listen on (default: 9999)
            timeout (int): Socket timeout in seconds (default: 60)
        """
        self.port = port
        self.timeout = timeout
        self.pc_control = None
        self.running = False
        self.server_socket = None
        self.clients = {}
        self.client_id_counter = 0
        self.logger = logging.getLogger('RemoteControlServer')

    def start_server(self, pc_control):
        """Start the remote control server."""
        self.pc_control = pc_control
        self.running = True
        
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.settimeout(5)  # Allow periodic checks for self.running
            self.server_socket.bind(('0.0.0.0', self.port))
            self.server_socket.listen(5)
            
            self.logger.info(f"Server started on port {self.port}")
            
            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    client_socket.settimeout(self.timeout)
                    
                    client_id = self.client_id_counter
                    self.client_id_counter += 1
                    
                    self.logger.info(f"New connection from {client_address} (ID: {client_id})")
                    
                    # Start a new thread for each client
                    client_thread = threading.Thread(
                        target=self.handle_client,
                        args=(client_socket, client_address, client_id),
                        daemon=True
                    )
                    self.clients[client_id] = {
                        'thread': client_thread,
                        'socket': client_socket,
                        'address': client_address
                    }
                    client_thread.start()
                    
                except socket.timeout:
                    continue  # Normal timeout for checking self.running
                except Exception as e:
                    self.logger.error(f"Accept error: {e}")
                    break
                
        except Exception as e:
            self.logger.error(f"Server error: {e}")
        finally:
            self.stop_server()
            self.logger.info("Server stopped")

    def handle_client(self, client_socket, client_address, client_id):
        """Handle communication with a connected client."""
        try:
            while self.running:
                try:
                    data = client_socket.recv(1024).decode().strip()
                    if not data:
                        break  # Client disconnected
                        
                    self.logger.info(f"Received from {client_address} (ID: {client_id}): {data}")
                    response = self.process_command(data)
                    
                    if response is not None:
                        client_socket.sendall(response.encode())
                        
                except socket.timeout:
                    # Send keepalive
                    client_socket.sendall(b"ALIVE")
                    continue
                except Exception as e:
                    self.logger.error(f"Client {client_id} error: {e}")
                    break
                    
        finally:
            client_socket.close()
            if client_id in self.clients:
                del self.clients[client_id]
            self.logger.info(f"Client {client_address} (ID: {client_id}) disconnected")

    def process_command(self, command):
        """Process incoming commands and return responses."""
        try:
            if command == "LOCK":
                self.pc_control.lock_pc()
                return "PC Locked"
                
            elif command == "SHUTDOWN":
                self.pc_control.shutdown_pc()
                return "PC Shutting down"
                
            elif command == "GET_NAME":
                import platform
                return platform.node()

            elif command == "GET_CURRENT_USER":
                return self.pc_control.current_user

            elif command == "GET_USAGE_LIMIT":
                if self.pc_control.usage_limit:
                    return str(self.pc_control.usage_limit)
                return "None"

            elif command == "GET_LOCK_TIMES":
                if self.pc_control.lock_times:
                    times = [f"{lt.hour}:{lt.minute:02d}" for lt in self.pc_control.lock_times]
                    return ",".join(times)
                return "None"

            elif command == "GET_TIME_REMAINING":
                remaining = self.pc_control.get_time_remaining()
                if remaining is not None:
                    return f"{int(remaining)} minutes"
                return "No limits set"

            elif command == "GET_ALLOWED_WINDOW":
                start, end = self.pc_control.get_allowed_window()
                if start is None or end is None:
                    return "OFF"
                return f"{_format_clock(start)}-{_format_clock(end)}"

            elif command.startswith("SET_ALLOWED_WINDOW:"):
                window = command.split(":", 1)[1].strip()
                try:
                    start_text, end_text = window.split("-", 1)
                    start = dtime(*map(int, start_text.split(":")))
                    end = dtime(*map(int, end_text.split(":")))
                except (ValueError, TypeError):
                    return "Invalid window format (use HH:MM-HH:MM)"
                # A window that starts and ends at the same time would lock the
                # PC around the clock; almost certainly a typo.
                if start == end:
                    return "Invalid window: start and end must differ"
                self.pc_control.set_allowed_window(start, end)
                return f"Allowed window set to {_format_clock(start)}-{_format_clock(end)}"

            elif command == "CLEAR_ALLOWED_WINDOW":
                self.pc_control.clear_allowed_window()
                return "Allowed window disabled"

            elif command.startswith("UNLOCK:"):
                try:
                    minutes = int(command.split(":", 1)[1])
                except ValueError:
                    return "Invalid unlock minutes"
                if minutes <= 0:
                    return "Invalid unlock minutes"
                until = self.pc_control.unlock_for(minutes)
                return f"Unlocked until {until:%H:%M}"

            elif command == "CANCEL_UNLOCK":
                if self.pc_control.cancel_unlock():
                    return "Temporary unlock cancelled"
                return "No active temporary unlock"

            elif command == "GET_UNLOCK_STATUS":
                until = self.pc_control.unlock_until
                if until and self.pc_control.is_unlock_active():
                    remaining = (until - datetime.now()).total_seconds() / 60
                    return f"ACTIVE {int(remaining)} minutes"
                return "INACTIVE"

            elif command == "GET_STATUS":
                actual_locked = self.pc_control.check_if_locked()
                if actual_locked != self.pc_control.is_locked:
                    self.pc_control.is_locked = actual_locked
                    self.logger.info(f"Status changed to: {'LOCKED' if actual_locked else 'UNLOCKED'}")
                return "LOCKED" if actual_locked else "UNLOCKED"
                
            elif command.startswith("MESSAGE:"):
                msg = command.split(":", 1)[1]
                self.pc_control.show_message(msg)
                return "Message sent"
                
            elif command.startswith("SET_LIMIT:"):
                try:
                    minutes = int(command.split(":", 1)[1])
                    # Apply the related fields atomically so the monitor thread
                    # never sees the new limit against the old usage counter.
                    with self.pc_control.state_lock:
                        self.pc_control.set_usage_limit(minutes)
                        self.pc_control.start_time = datetime.now()  # Reset day anchor when setting new limit
                        # A new limit grants a fresh budget: the child has not
                        # used it yet.
                        self.pc_control.accrued_seconds = 0.0
                        self.pc_control.last_saved_accrued = 0.0
                        self.pc_control.warnings_sent.clear()  # Clear warnings for new limit
                        self.pc_control.last_remaining = None  # Re-arm crossing detection
                    self.pc_control.save_state()  # Save state after setting limit
                    return f"Usage limit set to {minutes} minutes"
                except ValueError:
                    return "Invalid limit value"

            elif command.startswith("ADD_LOCK_TIME:"):
                try:
                    time_str = command.split(":", 1)[1]
                    hour, minute = map(int, time_str.split(":"))
                    self.pc_control.add_scheduled_lock(hour, minute)
                    self.pc_control.save_state()  # Save state after adding lock time
                    return f"Lock time added: {hour:02d}:{minute:02d}"
                except ValueError:
                    return "Invalid time format (use HH:MM)"
                    
            elif command.startswith("EXTEND_TIME:"):
                try:
                    minutes = int(command.split(":", 1)[1])
                    with self.pc_control.state_lock:
                        if self.pc_control.usage_limit:
                            self.pc_control.usage_limit += minutes
                            # Re-arm warnings so the new countdown warns again.
                            self.pc_control.warnings_sent.clear()
                        else:
                            return "No time limit set to extend"
                    self.pc_control.save_state()  # Save state after extending time
                    return f"Extended time by {minutes} minutes"
                except ValueError:
                    return "Invalid time value"

            elif command == "CLEAR_USAGE_LIMIT":
                with self.pc_control.state_lock:
                    self.pc_control.usage_limit = None
                    self.pc_control.warnings_sent.clear()
                self.pc_control.save_state()
                self.logger.info("Usage limit cleared")
                return "Usage limit cleared"

            elif command == "CLEAR_LOCK_TIMES":
                with self.pc_control.state_lock:
                    self.pc_control.lock_times = []
                    self.pc_control.warnings_sent.clear()  # Clear warnings too
                    self.pc_control.last_remaining = None  # Re-arm crossing detection
                self.pc_control.save_state()
                self.logger.info("All scheduled lock times cleared")
                return "All scheduled lock times cleared"

            elif command == "CLEAR_ALL":
                with self.pc_control.state_lock:
                    self.pc_control.usage_limit = None
                    self.pc_control.lock_times = []
                    self.pc_control.warnings_sent.clear()
                    self.pc_control.last_remaining = None  # Re-arm crossing detection
                self.pc_control.save_state()
                self.logger.info("All limits and locks cleared")
                return "All limits and locks cleared"

            elif command == "HELP":
                return (
                    "Available commands:\n"
                    "LOCK - Lock the PC\n"
                    "SHUTDOWN - Shutdown the PC\n"
                    "GET_NAME - Get PC name\n"
                    "GET_CURRENT_USER - Get current Windows username\n"
                    "GET_STATUS - Check if PC is locked\n"
                    "GET_USAGE_LIMIT - Get current usage limit\n"
                    "GET_LOCK_TIMES - Get scheduled lock times\n"
                    "GET_TIME_REMAINING - Get time until next lock\n"
                    "GET_ALLOWED_WINDOW - Get the allowed window (OFF if unset)\n"
                    "GET_UNLOCK_STATUS - Check the temporary unlock (ACTIVE/INACTIVE)\n"
                    "MESSAGE:<text> - Show popup message\n"
                    "SET_LIMIT:<minutes> - Set usage limit\n"
                    "ADD_LOCK_TIME:HH:MM - Add scheduled lock\n"
                    "SET_ALLOWED_WINDOW:HH:MM-HH:MM - Only allow use inside the window\n"
                    "UNLOCK:<minutes> - Grant temporary bonus time\n"
                    "EXTEND_TIME:<minutes> - Extend usage time\n"
                    "CLEAR_USAGE_LIMIT - Remove usage limit\n"
                    "CLEAR_LOCK_TIMES - Remove all scheduled locks\n"
                    "CLEAR_ALLOWED_WINDOW - Stop enforcing a window\n"
                    "CANCEL_UNLOCK - Revoke the temporary unlock\n"
                    "CLEAR_ALL - Clear all limits and locks"
                )
                
            else:
                return "Unknown command (try HELP)"
                
        except Exception as e:
            self.logger.error(f"Command processing error: {e}")
            return f"Error processing command: {e}"

    def stop_server(self):
        """Stop the server and clean up resources."""
        self.running = False
        
        # Close all client connections
        for client_id, client_info in list(self.clients.items()):
            try:
                client_info['socket'].close()
            except Exception as e:
                self.logger.error(f"Error closing client socket {client_id}: {e}")
            del self.clients[client_id]

        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception as e:
                self.logger.error(f"Error closing server socket: {e}")
            self.server_socket = None

    def __del__(self):
        """Destructor to ensure proper cleanup."""
        self.stop_server()

# Main
if __name__ == "__main__":
    # Create control instance
    control = PCTimeControl()
    
    # Add network connectivity check
    def check_port_availability(port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
            return True
        except socket.error:
            return False
    
    if not check_port_availability(9999):
        control.show_message(
            f"Port 9999 is already in use or blocked!\n"
            f"Check your firewall or other running applications.",
            "Network Error"
        )
        sys.exit(1)
    
    # Start remote control server
    remote = RemoteControlServer()
    server_thread = threading.Thread(target=remote.start_server, args=(control,))
    server_thread.daemon = True
    server_thread.start()

    # Verify server started
    time.sleep(1)  # Give server time to start
    if not remote.running:
        control.show_message(
            "Failed to start network server!\n"
            "Check firewall settings and try again.",
            "Server Error"
        )
        sys.exit(1)

    # Start the enforcement monitor only once the server is confirmed up, so a
    # failed startup can't lock the screen on its way to sys.exit(1).
    monitor_thread = threading.Thread(target=control.run_monitor, daemon=True)
    monitor_thread.start()

    print("Server is running. Press Ctrl+C to stop.")
    
    try:
        # Keep main thread alive while server runs
        while remote.running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down server...")
        remote.stop_server()
        server_thread.join(2)  # Wait up to 2 seconds for thread to finish
        print("Server stopped.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sys.exit(0)
