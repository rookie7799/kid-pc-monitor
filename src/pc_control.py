import os
import sys
import time
import datetime
import ctypes
import socket
import threading
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, time as dtime
import subprocess
from ctypes import wintypes
import getpass
import json

import logging
from pathlib import Path

# Windows API constants for session detection
WTS_CURRENT_SERVER_HANDLE = 0
WTSUserName = 5


def get_active_console_session():
    """
    Get the session ID of the active console session (physical keyboard/monitor).
    Returns the session ID or -1 if not found.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        return kernel32.WTSGetActiveConsoleSessionId()
    except Exception:
        return -1


def get_session_username(session_id):
    """
    Get the username for a given session ID using WTS API.
    Returns the username or None if not found.
    """
    try:
        wtsapi32 = ctypes.windll.wtsapi32

        # WTSQuerySessionInformationW
        WTSQuerySessionInformationW = wtsapi32.WTSQuerySessionInformationW
        WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
        WTSQuerySessionInformationW.restype = wintypes.BOOL

        # WTSFreeMemory
        WTSFreeMemory = wtsapi32.WTSFreeMemory
        WTSFreeMemory.argtypes = [wintypes.LPVOID]

        buffer = wintypes.LPWSTR()
        bytes_returned = wintypes.DWORD()

        if WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, session_id, WTSUserName,
                                       ctypes.byref(buffer), ctypes.byref(bytes_returned)):
            username = buffer.value
            WTSFreeMemory(buffer)
            return username if username else None
        return None
    except Exception:
        return None


def get_active_console_user():
    """
    Get the username of the user at the active console session.
    Falls back to getpass.getuser() if detection fails.
    """
    session_id = get_active_console_session()
    if session_id >= 0:
        username = get_session_username(session_id)
        if username:
            return username
    return getpass.getuser()


def is_console_session_locked():
    """
    Check if the active console session is locked.
    Uses a session-specific approach to detect lock status.
    """
    session_id = get_active_console_session()

    if session_id < 0:
        return False

    try:
        # Check if LogonUI.exe is running in the specific session
        # tasklist with /FI "SESSION eq X" filters by session
        out = subprocess.check_output(
            f'tasklist /FI "IMAGENAME eq LogonUI.exe" /FI "SESSION eq {session_id}" /NH',
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL
        )
        return "LogonUI.exe" in out
    except Exception:
        # Fallback: use the old method
        try:
            out = subprocess.check_output(
                'tasklist /FI "IMAGENAME eq LogonUI.exe" /NH',
                shell=True,
                text=True
            )
            return "LogonUI.exe" in out
        except Exception:
            return False

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

# Set up logging
log_file = 'pc_control.log'
if os.path.exists(log_file):
    os.unlink(log_file) #remove previous log

logging.basicConfig(
    filename=str(log_file),
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class PCTimeControl:
    def __init__(self):
        self.lock_times = []
        self.usage_limit = None
        self.start_time = datetime.now()
        self.is_locked = False
        self.last_activity = datetime.now()
        self.current_user = getpass.getuser()
        self.state_file = 'pc_control_state.json'
        self.logger = logging.getLogger('PCTimeControl')
        self.warnings_sent = set()  # Track which warnings have been sent
        self.warning_intervals = [15, 5, 1]  # Warning times in minutes before lock

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

    def should_monitor_user(self):
        """Check if current user should be monitored based on configuration"""
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

                # Restore start time (for usage tracking)
                if 'start_time' in state:
                    saved_start_time = datetime.fromisoformat(state['start_time'])
                    current_date = datetime.now().date()
                    saved_date = saved_start_time.date()

                    # If start_time is from a previous day, reset it to today
                    if saved_date < current_date:
                        self.start_time = datetime.now()
                        self.logger.info(f"Start time was from {saved_date}, reset to today")
                        print(f"[{datetime.now():%H:%M:%S}] Usage timer reset for new day")
                    else:
                        self.start_time = saved_start_time

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
                'start_time': self.start_time.isoformat(),
                'current_user': self.current_user
            }

            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)

            self.logger.info("State saved successfully")
        except Exception as e:
            self.logger.error(f"Error saving state: {e}")

    def check_if_locked(self):
        """
        Returns True if the active console session is locked, False otherwise.
        This is session-aware and correctly handles multi-user scenarios.
        """
        try:
            return is_console_session_locked()
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error checking lock status: {e}")
            return False

    def monitor_activity(self):
        """Monitor lock/unlock status"""
        while True:
            actual_locked = self.check_if_locked()

            # Detect unlock
            if self.is_locked and not actual_locked:
                self.is_locked = False
                print(f"[{datetime.now().strftime('%H:%M:%S')}] PC has been unlocked (detected by activity)")

            # Detect manual lock (not by our script)
            elif not self.is_locked and actual_locked:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] PC has been locked (detected)")

            time.sleep(3)  # Check every 3 seconds

    def add_scheduled_lock(self, hour, minute):
        """Add a time when the PC should be locked"""
        self.lock_times.append(dtime(hour, minute))

    def set_usage_limit(self, minutes):
        """Set maximum usage time in minutes"""
        self.usage_limit = minutes

    def show_message(self, message, title="PC Time Control"):
        """Display a message using tkinter"""
        def display():
            root = None
            try:
                root = tk.Tk()
                root.withdraw()  # Hide the main window
                root.attributes('-topmost', True)  # Make it appear on top

                # Auto-close after 60 seconds to prevent hanging
                root.after(60000, root.destroy)

                messagebox.showwarning(title, message)
            except Exception as e:
                self.logger.error(f"Error showing message: {e}")
                print(f"[{datetime.now():%H:%M:%S}] Error showing message: {e}")
            finally:
                if root:
                    try:
                        root.quit()
                        root.destroy()
                    except Exception:
                        pass  # Already destroyed

        # Run in a separate thread to avoid blocking
        threading.Thread(target=display, daemon=True).start()

    def lock_pc(self):
        """Lock the Windows PC"""
        try:
            self.is_locked = True
            ctypes.windll.user32.LockWorkStation()
            self.logger.info("PC locked successfully")
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

    def get_time_remaining(self):
        """Calculate minutes remaining until lock. Returns None if no limit set."""
        if not self.should_monitor_user():
            return None

        current_time = datetime.now()
        min_remaining = None

        # Check scheduled lock times
        for lock_time in self.lock_times:
            lock_datetime = current_time.replace(hour=lock_time.hour, minute=lock_time.minute, second=0, microsecond=0)

            # If lock time is earlier today, it's for tomorrow
            if lock_datetime <= current_time:
                lock_datetime = lock_datetime.replace(day=lock_datetime.day + 1)

            minutes_until_lock = (lock_datetime - current_time).total_seconds() / 60

            if min_remaining is None or minutes_until_lock < min_remaining:
                min_remaining = minutes_until_lock

        # Check usage limit
        if self.usage_limit:
            usage_minutes = (current_time - self.start_time).total_seconds() / 60
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

    def check_time_limits(self):
        """Check if any time limits have been reached"""
        # Skip all checks if user is exempt from monitoring
        if not self.should_monitor_user():
            return False, ""

        current_time = datetime.now()

        # Check scheduled lock times
        for lock_time in self.lock_times:
            if (current_time.hour == lock_time.hour and
                current_time.minute == lock_time.minute and
                current_time.second < 1):
                return True, "Scheduled lock time reached"

        # Check usage limit
        if self.usage_limit:
            usage_minutes = (current_time - self.start_time).total_seconds() / 60
            if usage_minutes >= self.usage_limit:
                return True, f"Usage limit of {self.usage_limit} minutes reached"

        return False, ""

    def run_monitor(self):
        """Main monitoring loop"""
        print("PC Time Control is running...")
        while True:
            # Check and send warnings if approaching time limit
            self.check_and_send_warnings()

            # Check if time limit reached
            should_lock, reason = self.check_time_limits()
            if should_lock:
                print(f"Locking PC: {reason}")
                self.lock_pc()
                break
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
                # Return the user at the active console, not the process owner
                return get_active_console_user()

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
                    self.pc_control.start_time = datetime.now()  # Reset start time when setting new limit
                    self.pc_control.warnings_sent.clear()  # Clear warnings for new limit
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

            elif command == "CLEAR_LOCK_TIMES":
                self.pc_control.lock_times = []
                self.pc_control.warnings_sent.clear()  # Clear warnings too
                self.pc_control.save_state()
                self.logger.info("All scheduled lock times cleared")
                return "All scheduled lock times cleared"

            elif command == "CLEAR_ALL":
                self.pc_control.usage_limit = None
                self.pc_control.lock_times = []
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
                    "GET_LOCK_TIMES - Get scheduled lock times\n"
                    "GET_TIME_REMAINING - Get time until next lock\n"
                    "MESSAGE:<text> - Show popup message\n"
                    "SET_LIMIT:<minutes> - Set usage limit\n"
                    "ADD_LOCK_TIME:HH:MM - Add scheduled lock\n"
                    "EXTEND_TIME:<minutes> - Extend usage time\n"
                    "CLEAR_USAGE_LIMIT - Remove usage limit\n"
                    "CLEAR_LOCK_TIMES - Remove all scheduled locks\n"
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
