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
except ImportError:  # Allows logic tests on systems without Tk installed.
    tk = None
    messagebox = None
from datetime import datetime, time as dtime, timedelta
import subprocess
from ctypes import wintypes
import getpass
import json

import logging
from pathlib import Path

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
    MONITOR_INTERVAL_SECONDS = 1
    LOCK_RETRY_SECONDS = 10

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
            pass
        return None

    def __init__(self):
        self.usage_limit = None
        self.start_time = datetime.now()
        self.accrued_seconds = 0.0  # Active (unlocked) usage time accrued this day
        self.is_locked = False
        self.last_activity = datetime.now()
        self.current_user = self._get_console_user() or getpass.getuser()
        self.state_file = DATA_DIR / 'pc_control_state.json'
        self.logger = logging.getLogger('PCTimeControl')
        self.warnings_sent = set()  # Track which warnings have been sent
        self.warning_intervals = [15, 5, 1]  # Warning times in minutes before lock
        # Allowed-usage window (default 07:00-22:00). Outside it the PC is locked.
        self.allowed_start = dtime(7, 0)
        self.allowed_end = dtime(22, 0)
        # Temporary override: PC is unlocked until this datetime even if outside
        # the window. None = no override. Does NOT reset usage counters.
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

    def refresh_current_user(self):
        """Update current_user from the active console session."""
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

                # Restore usage limit
                if 'usage_limit' in state:
                    self.usage_limit = state['usage_limit']

                # Restore accrued active usage time (default 0 for old states)
                if 'accrued_seconds' in state:
                    self.accrued_seconds = float(state['accrued_seconds'])
                else:
                    self.accrued_seconds = 0.0

                # Restore allowed window (default 07:00-22:00 for old states)
                if 'allowed_start' in state:
                    self.allowed_start = dtime(*map(int, state['allowed_start'].split(':')))
                if 'allowed_end' in state:
                    self.allowed_end = dtime(*map(int, state['allowed_end'].split(':')))

                # Restore temporary unlock override (if still in the future)
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

                    # A usage limit is daily. Never carry elapsed time across
                    # a date boundary (also recover safely from a future date
                    # caused by a clock correction). Both the start_time AND the
                    # accrued usage must reset — otherwise yesterday's exhausted
                    # limit "sticks" to the new day and the PC shows 0 minutes
                    # remaining right after boot (bug fixed 2026-08).
                    if saved_date != current_date:
                        self.start_time = datetime.now()
                        self.accrued_seconds = 0.0
                        self.logger.info(f"Start time was from {saved_date}, reset to today")
                        print(f"[{datetime.now():%H:%M:%S}] Usage timer reset for new day")
                    else:
                        self.start_time = saved_start_time

                self.logger.info(f"State loaded: usage limit: {self.usage_limit}")
                print(f"[{datetime.now():%H:%M:%S}] Loaded previous settings from {self.state_file}")
        except Exception as e:
            self.logger.error(f"Error loading state: {e}")
            print(f"[{datetime.now():%H:%M:%S}] Could not load previous state: {e}")

    def save_state(self):
        """Save current state to JSON file"""
        try:
            state = {
                'usage_limit': self.usage_limit,
                'accrued_seconds': round(self.accrued_seconds, 1),
                'allowed_start': f"{self.allowed_start.hour:02d}:{self.allowed_start.minute:02d}",
                'allowed_end': f"{self.allowed_end.hour:02d}:{self.allowed_end.minute:02d}",
                'unlock_until': self.unlock_until.isoformat() if self.unlock_until else None,
                'start_time': self.start_time.isoformat(),
                'current_user': self.current_user
            }

            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)

            self.logger.info("State saved successfully")
        except Exception as e:
            self.logger.error(f"Error saving state: {e}")

    def reset_daily_usage_if_needed(self, current_time=None):
        """Reset the daily usage window when the calendar day changes."""
        current_time = current_time or datetime.now()
        if self.start_time.date() == current_time.date():
            return False

        previous_date = self.start_time.date()
        self.start_time = current_time
        self.accrued_seconds = 0.0
        self.warnings_sent.clear()
        self.save_state()
        self.logger.info(
            "Usage timer reset for new day (previous start date: %s)",
            previous_date,
        )
        return True

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

            # Accrue active usage only while the PC is unlocked AND the console
            # user is monitored. A lock (or an exempt parent) pauses the timer.
            now = time.monotonic()
            delta = now - last
            last = now
            if not self.is_locked and self.should_monitor_user():
                self.reset_daily_usage_if_needed()
                if self.usage_limit:
                    self.accrued_seconds += delta
                    if int(self.accrued_seconds) % 30 == 0:
                        self.save_state()

            time.sleep(3)  # Check every 3 seconds

    def set_usage_limit(self, minutes):
        """Set maximum usage time in minutes"""
        self.usage_limit = minutes

    def show_message(self, message, title="PC Time Control"):
        """Display a message to the current user.

        Uses msg.exe (works from any session, including Session 0) with a
        tkinter fallback for interactive desktop sessions.
        """
        def _show_via_msg():
            try:
                # Use the current console user, not the cached value
                user = self._get_console_user() or self.current_user
                subprocess.run(
                    ['msg', user, '/TIME:60', message],
                    capture_output=True, timeout=65,
                )
                self.logger.info(f"Message sent to {user} via msg.exe")
            except Exception as e:
                self.logger.error(f"msg.exe failed: {e}")

        def _show_via_tk():
            root = None
            try:
                if tk is None or messagebox is None:
                    raise RuntimeError("Tkinter is not available")
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                root.after(60000, root.destroy)
                messagebox.showwarning(title, message)
            except Exception as e:
                self.logger.error(f"Tkinter message failed: {e}")
            finally:
                if root:
                    try:
                        root.quit()
                        root.destroy()
                    except Exception:
                        pass

        # Try msg.exe first (works from Session 0), fall back to tkinter
        threading.Thread(target=_show_via_msg, daemon=True).start()

    def lock_pc(self):
        """Lock the Windows PC (active console session).

        When running as SYSTEM (Session 0), LockWorkStation locks the wrong
        session.  We locate the console session ID via ``query session``
        (the word "console" in the SESSIONNAME column is always English)
        and log it off, which forces the lock screen.
        """
        try:
            self.is_locked = True
            result = subprocess.run(
                'query session', shell=True,
                capture_output=True, text=True, timeout=10,
            )
            session_id = None
            for line in result.stdout.splitlines():
                if 'console' in line.lower():
                    parts = line.split()
                    for p in parts:
                        if p.isdigit():
                            session_id = p
                            break
                    break
            if session_id:
                subprocess.run(
                    ['logoff', session_id],
                    capture_output=True, timeout=10,
                )
                self.logger.info("PC locked (session %s)", session_id)
            else:
                ctypes.windll.user32.LockWorkStation()
                self.logger.info("PC locked (fallback)")
        except Exception as e:
            self.logger.error(f"Error locking PC: {e}")
            print(f"[{datetime.now():%H:%M:%S}] Error locking PC: {e}")

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

    def is_unlock_active(self, current_time=None):
        """Return True while a temporary unlock override is active.

        Clears an expired override so the PC re-locks once the grant runs out.
        """
        current_time = current_time or datetime.now()
        if self.unlock_until and current_time < self.unlock_until:
            return True
        if self.unlock_until and current_time >= self.unlock_until:
            self.unlock_until = None
            self.save_state()
        return False

    def is_within_allowed_window(self, current_time=None):
        """Return True if current time is inside the allowed usage window.

        A temporary unlock override (unlock_until) lets the PC be used outside
        the window without resetting the usage counters.
        """
        current_time = current_time or datetime.now()
        # Temporary override is active: allow regardless of the window.
        if self.is_unlock_active(current_time):
            return True
        now = dtime(current_time.hour, current_time.minute)
        if self.allowed_start <= self.allowed_end:
            return self.allowed_start <= now <= self.allowed_end
        # Window crosses midnight (e.g. 22:00-07:00): outside is locked.
        return now >= self.allowed_start or now <= self.allowed_end

    def unlock_for(self, minutes):
        """Grant a one-time unlock for `minutes` minutes beyond the window.

        Does NOT reset accrued usage time or the daily limit; the daily limit
        continues to apply inside the override.
        """
        self.unlock_until = datetime.now() + timedelta(minutes=minutes)
        self.save_state()
        self.logger.info("Temporary unlock granted until %s", self.unlock_until)
        return self.unlock_until

    def set_allowed_window(self, start, end):
        """Set the allowed usage window (start/end as datetime.time)."""
        self.allowed_start = start
        self.allowed_end = end
        self.save_state()
        self.logger.info(
            "Allowed window set to %02d:%02d - %02d:%02d",
            start.hour, start.minute, end.hour, end.minute,
        )

    def get_time_remaining(self, current_time=None):
        """Calculate minutes remaining until lock. Returns None if no limit set."""
        if not self.should_monitor_user():
            return None

        current_time = current_time or datetime.now()
        self.reset_daily_usage_if_needed(current_time)
        min_remaining = None

        # Check usage limit
        if self.usage_limit:
            usage_minutes = self.accrued_seconds / 60.0
            minutes_until_limit = self.usage_limit - usage_minutes

            if min_remaining is None or minutes_until_limit < min_remaining:
                min_remaining = minutes_until_limit

        return min_remaining

    def check_and_send_warnings(self):
        """Check if warnings should be sent and send them"""
        time_remaining = self.get_time_remaining()

        if time_remaining is None:
            return

        # Check each warning interval
        for warning_mins in self.warning_intervals:
            warning_key = f"{warning_mins}min"

            # If we're within the warning window and haven't sent this warning yet
            if time_remaining <= warning_mins and warning_key not in self.warnings_sent:
                self.warnings_sent.add(warning_key)

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

        # A temporary unlock override grants access regardless of the daily
        # limit AND the allowed window. It is a parent-granted extension of
        # screen time, so it must work even when the daily budget is spent.
        if self.is_unlock_active(current_time):
            return False, ""

        # Check usage limit
        if self.usage_limit:
            usage_minutes = self.accrued_seconds / 60.0
            if usage_minutes >= self.usage_limit:
                return True, f"Usage limit of {self.usage_limit} minutes reached"

        # Outside the allowed window the PC must be locked (re-locks on
        # re-entry outside the window). This overrides the daily usage limit
        # because availability is the harder constraint.
        if not self.is_within_allowed_window(current_time):
            return True, "Outside allowed usage window"

        return False, ""

    def run_monitor(self):
        """Continuously enforce limits, including after a manual unlock."""
        print("PC Time Control is running...")
        while True:
            try:
                # Check and send warnings if approaching time limit
                self.check_and_send_warnings()

                # Keep enforcing an expired limit. Avoid repeatedly calling
                # LockWorkStation while the workstation is already locked;
                # after an unlock the next retry locks it again.
                should_lock, reason = self.check_time_limits()
                if should_lock and not self.check_if_locked():
                    print(f"Locking PC: {reason}")
                    self.lock_pc()
                    time.sleep(self.LOCK_RETRY_SECONDS)
                    continue

                time.sleep(self.MONITOR_INTERVAL_SECONDS)
            except Exception:
                self.logger.exception("Unexpected error in monitoring loop")
                time.sleep(self.LOCK_RETRY_SECONDS)

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

            elif command == "GET_ALLOWED_WINDOW":
                return (f"{self.pc_control.allowed_start.hour:02d}:{self.pc_control.allowed_start.minute:02d}"
                        f"-{self.pc_control.allowed_end.hour:02d}:{self.pc_control.allowed_end.minute:02d}")

            elif command.startswith("SET_ALLOWED_WINDOW:"):
                try:
                    start_s, end_s = command.split(":", 1)[1].split("-", 1)
                    sh, sm = map(int, start_s.split(":"))
                    eh, em = map(int, end_s.split(":"))
                    self.pc_control.set_allowed_window(dtime(sh, sm), dtime(eh, em))
                    return (f"Allowed window set to {sh:02d}:{sm:02d}-{eh:02d}:{em:02d}")
                except (ValueError, TypeError):
                    return "Invalid format (use HH:MM-HH:MM)"

            elif command.startswith("UNLOCK:"):
                try:
                    minutes = int(command.split(":", 1)[1])
                    if minutes <= 0:
                        return "Invalid unlock duration"
                    until = self.pc_control.unlock_for(minutes)
                    return f"Temporary unlock until {until.strftime('%H:%M')}"
                except (ValueError, TypeError):
                    return "Invalid format (use UNLOCK:<minutes>)"

            elif command == "CANCEL_UNLOCK":
                if self.pc_control.unlock_until:
                    self.pc_control.unlock_until = None
                    self.pc_control.save_state()
                    self.pc_control.logger.info("Temporary unlock cancelled")
                    return "Temporary unlock cancelled"
                return "No active temporary unlock"

            elif command == "GET_UNLOCK_STATUS":
                if self.pc_control.unlock_until:
                    remaining = (self.pc_control.unlock_until - datetime.now()).total_seconds() / 60
                    if remaining > 0:
                        return f"ACTIVE {int(remaining)} minutes"
                return "INACTIVE"

            elif command == "GET_TIME_REMAINING":
                remaining = self.pc_control.get_time_remaining()
                if remaining is not None:
                    return f"{int(remaining)} minutes"
                return "No limits set"

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
                    self.pc_control.set_usage_limit(minutes)
                    self.pc_control.start_time = datetime.now()  # Reset day anchor when setting new limit
                    self.pc_control.accrued_seconds = 0.0  # Reset active usage for the new limit
                    self.pc_control.warnings_sent.clear()  # Clear warnings for new limit
                    self.pc_control.save_state()  # Save state after setting limit
                    return f"Usage limit set to {minutes} minutes"
                except ValueError:
                    return "Invalid limit value"

            elif command.startswith("EXTEND_TIME:"):
                try:
                    minutes = int(command.split(":", 1)[1])
                    if self.pc_control.usage_limit:
                        self.pc_control.usage_limit += minutes
                        self.pc_control.save_state()  # Save state after extending time
                        return f"Extended time by {minutes} minutes"
                    return "No time limit set to extend"
                except ValueError:
                    return "Invalid time value"

            elif command == "CLEAR_USAGE_LIMIT":
                self.pc_control.usage_limit = None
                self.pc_control.save_state()
                self.logger.info("Usage limit cleared")
                return "Usage limit cleared"

            elif command == "CLEAR_ALL":
                self.pc_control.usage_limit = None
                self.pc_control.warnings_sent.clear()
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
                    "GET_TIME_REMAINING - Get time until next lock\n"
                    "GET_ALLOWED_WINDOW - Get allowed usage window\n"
                    "SET_ALLOWED_WINDOW:HH:MM-HH:MM - Set allowed usage window\n"
                    "UNLOCK:<minutes> - Temporary unlock outside the window\n"
                    "CANCEL_UNLOCK - Cancel temporary unlock\n"
                    "GET_UNLOCK_STATUS - Active temporary unlock remaining\n"
                    "MESSAGE:<text> - Show popup message\n"
                    "SET_LIMIT:<minutes> - Set usage limit\n"
                    "EXTEND_TIME:<minutes> - Extend usage time\n"
                    "CLEAR_USAGE_LIMIT - Remove usage limit\n"
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

    # Enforce automatic usage and scheduled limits independently from the
    # remote-control server.
    monitor_thread = threading.Thread(target=control.run_monitor, daemon=True)
    monitor_thread.start()
    
    # Verify server started
    time.sleep(1)  # Give server time to start
    if not remote.running:
        control.show_message(
            "Failed to start network server!\n"
            "Check firewall settings and try again.",
            "Server Error"
        )
        sys.exit(1)
    
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
