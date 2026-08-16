#!/usr/bin/env python3
"""
Kid PC Monitor - Linux/Arch Agent
Controls screen time and enforces restrictions on Linux systems.
"""

import os
import sys
import time
import datetime
import socket
import threading
import subprocess
import getpass
import json
import logging
from pathlib import Path
from datetime import datetime as dt, time as dtime

# ============================================
# CONFIGURATION
# ============================================

# List of Linux usernames to monitor (leave empty to monitor all users)
# Example: MONITORED_USERS = ['tommy', 'sarah', 'kid1']
MONITORED_USERS = []

# List of Linux usernames to EXEMPT from monitoring (parents/admins)
# Example: EXEMPT_USERS = ['pavel', 'mom', 'dad', 'root']
EXEMPT_USERS = []

# If both lists are empty, ALL users will be monitored
# If MONITORED_USERS has entries, ONLY those users are monitored
# If EXEMPT_USERS has entries, everyone EXCEPT those users is monitored

# ============================================

# Set up logging
log_file = 'pc_control.log'
if os.path.exists(log_file):
    os.unlink(log_file)

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
        self.start_time = dt.now()
        self.is_locked = False
        self.last_activity = dt.now()
        self.current_user = getpass.getuser()
        self.state_file = 'pc_control_state.json'
        self.logger = logging.getLogger('PCTimeControl')
        self.warnings_sent = set()
        self.warning_intervals = [15, 5, 1]  # Warning times in minutes before lock

        if self.should_monitor_user():
            self.logger.info(f"Monitoring enabled for user: {self.current_user}")
            print(f"[{dt.now():%H:%M:%S}] Monitoring user: {self.current_user}")
        else:
            self.logger.info(f"User {self.current_user} is EXEMPT from monitoring")
            print(f"[{dt.now():%H:%M:%S}] User {self.current_user} is EXEMPT - no restrictions will apply")

        self.load_state()
        self.monitor_thread = threading.Thread(target=self.monitor_activity, daemon=True)
        self.monitor_thread.start()

    def should_monitor_user(self):
        """Check if current user should be monitored based on configuration"""
        if MONITORED_USERS:
            return self.current_user in MONITORED_USERS
        if EXEMPT_USERS:
            return self.current_user not in EXEMPT_USERS
        return True

    def load_state(self):
        """Load saved state from JSON file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                if 'lock_times' in state:
                    self.lock_times = [dtime(*map(int, t.split(':'))) for t in state['lock_times']]
                if 'usage_limit' in state:
                    self.usage_limit = state['usage_limit']
                if 'start_time' in state:
                    saved_start_time = dt.fromisoformat(state['start_time'])
                    current_date = dt.now().date()
                    saved_date = saved_start_time.date()
                    if saved_date < current_date:
                        self.start_time = dt.now()
                        self.logger.info(f"Start time was from {saved_date}, reset to today")
                        print(f"[{dt.now():%H:%M:%S}] Usage timer reset for new day")
                    else:
                        self.start_time = saved_start_time
                self.logger.info(f"State loaded: {len(self.lock_times)} lock times, usage limit: {self.usage_limit}")
                print(f"[{dt.now():%H:%M:%S}] Loaded previous settings from {self.state_file}")
        except Exception as e:
            self.logger.error(f"Error loading state: {e}")
            print(f"[{dt.now():%H:%M:%S}] Could not load previous state: {e}")

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
        """Returns True if screen is locked, False if desktop is active."""
        try:
            if os.environ.get('DISPLAY'):
                result = subprocess.run(['xset', 'q'], capture_output=True, timeout=2)
                if result.returncode == 0:
                    return False
            if os.environ.get('WAYLAND_DISPLAY'):
                return False
            return False
        except Exception as e:
            print(f"[{dt.now():%H:%M:%S}] Error checking screen state: {e}")
            return False

    def monitor_activity(self):
        """Monitor lock/unlock status"""
        while True:
            actual_locked = self.check_if_locked()
            if self.is_locked and not actual_locked:
                self.is_locked = False
                print(f"[{dt.now().strftime('%H:%M:%S')}] Screen has been unlocked")
            elif not self.is_locked and actual_locked:
                print(f"[{dt.now().strftime('%H:%M:%S')}] Screen has been locked")
            time.sleep(3)

    def add_scheduled_lock(self, hour, minute):
        self.lock_times.append(dtime(hour, minute))

    def set_usage_limit(self, minutes):
        self.usage_limit = minutes

    def show_notification(self, message, title="PC Time Control"):
        """Display notification using notify-send"""
        try:
            subprocess.run(['notify-send', title, message], timeout=5, capture_output=True)
            self.logger.info(f"Notification sent: {message}")
            print(f"[{dt.now():%H:%M:%S}] Notification: {message}")
        except Exception as e:
            self.logger.error(f"Error showing notification: {e}")

    def lock_screen(self):
        """Lock the screen on Linux"""
        try:
            self.is_locked = True
            lock_commands = [
                ['i3lock', '-c', '000000'],
                ['gnome-screensaver-command', '--lock'],
                ['loginctl', 'lock-session'],
                ['xfce4-screensaver-command', '--lock'],
                ['kdelock'],
            ]
            for cmd in lock_commands:
                try:
                    subprocess.run(cmd, timeout=5, capture_output=True)
                    self.logger.info(f"Screen locked using {cmd[0]}")
                    print(f"[{dt.now():%H:%M:%S}] Screen locked")
                    return
                except (FileNotFoundError, Exception):
                    continue
            self.logger.warning("No lock mechanism available")
            print(f"[{dt.now():%H:%M:%S}] Warning: Could not lock screen")
        except Exception as e:
            self.logger.error(f"Error locking screen: {e}")

    def shutdown_system(self, seconds=60):
        """Shutdown system with warning"""
        try:
            subprocess.run(['sudo', 'shutdown', '-h', f'+{seconds//60}'], timeout=5, capture_output=True)
            self.logger.info(f"Shutdown initiated ({seconds}s)")
            print(f"[{dt.now():%H:%M:%S}] Shutdown initiated")
        except Exception as e:
            self.logger.error(f"Error initiating shutdown: {e}")

    def get_time_remaining(self):
        """Calculate minutes remaining until lock."""
        if not self.should_monitor_user():
            return None
        current_time = dt.now()
        min_remaining = None
        for lock_time in self.lock_times:
            lock_datetime = current_time.replace(hour=lock_time.hour, minute=lock_time.minute, second=0, microsecond=0)
            if lock_datetime <= current_time:
                lock_datetime = lock_datetime.replace(day=lock_datetime.day + 1)
            minutes_until_lock = (lock_datetime - current_time).total_seconds() / 60
            if min_remaining is None or minutes_until_lock < min_remaining:
                min_remaining = minutes_until_lock
        if self.usage_limit:
            usage_minutes = (current_time - self.start_time).total_seconds() / 60
            minutes_until_limit = self.usage_limit - usage_minutes
            if min_remaining is None or minutes_until_limit < min_remaining:
                min_remaining = minutes_until_limit
        return min_remaining

    def check_and_send_warnings(self):
        """Check if warnings should be sent"""
        time_remaining = self.get_time_remaining()
        if time_remaining is None:
            return
        for warning_mins in self.warning_intervals:
            warning_key = f"{warning_mins}min"
            if time_remaining <= warning_mins and warning_key not in self.warnings_sent:
                self.warnings_sent.add(warning_key)
                msg = f"⚠️ Computer will lock in {warning_mins} minute{'s' if warning_mins != 1 else ''}!"
                self.show_notification(msg, "Warning")
                print(f"[{dt.now():%H:%M:%S}] Warning: {warning_mins} minutes until lock")

    def check_time_limits(self):
        """Check if any time limits have been reached"""
        if not self.should_monitor_user():
            return False, ""
        current_time = dt.now()
        for lock_time in self.lock_times:
            if (current_time.hour == lock_time.hour and current_time.minute == lock_time.minute and current_time.second < 1):
                return True, "Scheduled lock time reached"
        if self.usage_limit:
            usage_minutes = (current_time - self.start_time).total_seconds() / 60
            if usage_minutes >= self.usage_limit:
                return True, f"Usage limit of {self.usage_limit} minutes reached"
        return False, ""


class RemoteControlServer:
    def __init__(self, port=9999, timeout=60):
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
            self.server_socket.settimeout(5)
            self.server_socket.bind(('0.0.0.0', self.port))
            self.server_socket.listen(5)
            self.logger.info(f"Server started on port {self.port}")
            print(f"[{dt.now():%H:%M:%S}] Server listening on port {self.port}")
            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    client_socket.settimeout(self.timeout)
                    client_id = self.client_id_counter
                    self.client_id_counter += 1
                    self.logger.info(f"New connection from {client_address} (ID: {client_id})")
                    client_thread = threading.Thread(target=self.handle_client, args=(client_socket, client_address, client_id), daemon=True)
                    self.clients[client_id] = {'thread': client_thread, 'socket': client_socket, 'address': client_address}
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    self.logger.error(f"Accept error: {e}")
                    break
        except Exception as e:
            self.logger.error(f"Server error: {e}")
        finally:
            self.stop_server()

    def handle_client(self, client_socket, client_address, client_id):
        """Handle communication with a connected client."""
        try:
            while self.running:
                try:
                    data = client_socket.recv(1024).decode().strip()
                    if not data:
                        break
                    self.logger.info(f"Received from {client_address}: {data}")
                    response = self.process_command(data)
                    if response is not None:
                        client_socket.sendall(response.encode())
                except socket.timeout:
                    client_socket.sendall(b"ALIVE")
                except Exception as e:
                    self.logger.error(f"Client error: {e}")
                    break
        finally:
            client_socket.close()
            if client_id in self.clients:
                del self.clients[client_id]

    def process_command(self, command):
        """Process incoming commands."""
        try:
            if command == "LOCK":
                self.pc_control.lock_screen()
                return "Screen Locked"
            elif command == "SHUTDOWN":
                self.pc_control.shutdown_system()
                return "System Shutting down"
            elif command == "GET_NAME":
                import platform
                return platform.node()
            elif command == "GET_CURRENT_USER":
                return self.pc_control.current_user
            elif command == "GET_USAGE_LIMIT":
                return str(self.pc_control.usage_limit) if self.pc_control.usage_limit else "None"
            elif command == "GET_LOCK_TIMES":
                if self.pc_control.lock_times:
                    return ",".join([f"{lt.hour}:{lt.minute:02d}" for lt in self.pc_control.lock_times])
                return "None"
            elif command == "GET_TIME_REMAINING":
                remaining = self.pc_control.get_time_remaining()
                return f"{int(remaining)} minutes" if remaining else "No limits set"
            elif command == "GET_STATUS":
                return "UNLOCKED"
            elif command.startswith("MESSAGE:"):
                self.pc_control.show_notification(command.split(":", 1)[1])
                return "Message sent"
            elif command.startswith("SET_LIMIT:"):
                self.pc_control.set_usage_limit(int(command.split(":", 1)[1]))
                self.pc_control.start_time = dt.now()
                self.pc_control.warnings_sent.clear()
                self.pc_control.save_state()
                return f"Usage limit set"
            elif command.startswith("ADD_LOCK_TIME:"):
                time_str = command.split(":", 1)[1]
                hour, minute = map(int, time_str.split(":"))
                self.pc_control.add_scheduled_lock(hour, minute)
                self.pc_control.save_state()
                return f"Lock time added: {hour:02d}:{minute:02d}"
            elif command == "CLEAR_USAGE_LIMIT":
                self.pc_control.usage_limit = None
                self.pc_control.save_state()
                return "Usage limit cleared"
            elif command == "CLEAR_LOCK_TIMES":
                self.pc_control.lock_times = []
                self.pc_control.warnings_sent.clear()
                self.pc_control.save_state()
                return "All scheduled lock times cleared"
            elif command == "CLEAR_ALL":
                self.pc_control.usage_limit = None
                self.pc_control.lock_times = []
                self.pc_control.warnings_sent.clear()
                self.pc_control.save_state()
                return "All limits and locks cleared"
            elif command == "HELP":
                return "Available commands: LOCK, SHUTDOWN, GET_NAME, GET_CURRENT_USER, GET_STATUS, GET_USAGE_LIMIT, GET_LOCK_TIMES, GET_TIME_REMAINING, MESSAGE:<text>, SET_LIMIT:<minutes>, ADD_LOCK_TIME:HH:MM, CLEAR_USAGE_LIMIT, CLEAR_LOCK_TIMES, CLEAR_ALL"
            else:
                return "Unknown command"
        except Exception as e:
            self.logger.error(f"Command error: {e}")
            return f"Error: {e}"

    def stop_server(self):
        """Stop the server and clean up."""
        self.running = False
        for client_id, client_info in list(self.clients.items()):
            try:
                client_info['socket'].close()
            except:
                pass
            del self.clients[client_id]
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass


if __name__ == "__main__":
    control = PCTimeControl()
    remote = RemoteControlServer()
    server_thread = threading.Thread(target=remote.start_server, args=(control,))
    server_thread.daemon = True
    server_thread.start()
    time.sleep(1)
    print("Server is running. Press Ctrl+C to stop.")
    try:
        while remote.running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        remote.stop_server()
        server_thread.join(2)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sys.exit(0)
