import os
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

class PCTimeControl:
    def init(self):
        self.lock_times = []
        self.usage_limit = None
        self.start_time = datetime.now()
        self.is_locked = False
        self.last_activity = datetime.now()

        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self.monitor_activity, daemon=True)
        self.monitor_thread.start()

    def _enum_callback(self, hwnd, lParam):
        # build a list of visible, titled windows
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                self.visible_windows.append(hwnd)
        return True

    def _check_if_locked(self):
        return self.is_locked

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
            print(f"[{datetime.now():%H:%M:%S}] LogonUI.exe running? {locked}")
            return locked
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error checking LogonUI: {e}")
            # fallback to whatever you had before (or assume unlocked)
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

    def is_workstation_locked(self):
        """Check if the workstation is currently locked"""
        # Simple method: check if we can get the foreground window title
        try:
            user32 = ctypes.windll.user32
            h_wnd = user32.GetForegroundWindow()

            # If no foreground window, likely locked
            if h_wnd == 0:
                return True

            # Try to get window title length
            length = user32.GetWindowTextLengthW(h_wnd)

            # If we can't get window info, likely locked
            if length == 0:
                # Could be locked or just an app with no title
                # For now, assume not locked unless we're sure
                return False

            return False
        except:
            return False

    def add_scheduled_lock(self, hour, minute):
        """Add a time when the PC should be locked"""
        self.lock_times.append(dtime(hour, minute))

    def set_usage_limit(self, minutes):
        """Set maximum usage time in minutes"""
        self.usage_limit = minutes

    def show_message(self, message, title="PC Time Control"):
        """Display a message using tkinter"""
        def display():
            root = tk.Tk()
            root.withdraw()  # Hide the main window
            root.attributes('-topmost', True)  # Make it appear on top
            messagebox.showwarning(title, message)
            root.destroy()

        # Run in a separate thread to avoid blocking
        threading.Thread(target=display, daemon=True).start()

    def lock_pc(self):
        """Lock the Windows PC"""
        self.is_locked = True
        ctypes.windll.user32.LockWorkStation()

    def shutdown_pc(self, seconds=60):
        """Shutdown PC with warning"""
        os.system(f'shutdown /s /t {seconds} /c "Computer will shutdown in {seconds} seconds"')

    def cancel_shutdown(self):
        """Cancel pending shutdown"""
        os.system('shutdown /a')

    def check_time_limits(self):
        """Check if any time limits have been reached"""
        current_time = datetime.now()

        # Check scheduled lock times
        for lock_time in self.lock_times:
            if (current_time.hour == lock_time.hour and
                current_time.minute == lock_time.minute and
                current_time.second < 1):
                return True, "Scheduled lock time reached"

        # Check usage limit
        if self.usage_limit:
            usage_minutes = (current_time - self.start_time).seconds / 60
            if usage_minutes >= self.usage_limit:
                return True, f"Usage limit of {self.usage_limit} minutes reached"

        return False, ""

    def run_monitor(self):
        """Main monitoring loop"""
        print("PC Time Control is running...")
        while True:
            should_lock, reason = self.check_time_limits()
            if should_lock:
                print(f"Locking PC: {reason}")
                # Give 1 minute warning
                self.show_message("Computer will lock in 1 minute!", "Warning")
                time.sleep(60)
                self.lock_pc()
                break
            time.sleep(1)

# Simple Remote Control Server
class RemoteControlServer:
    def init(self, port=9999):
        self.port = port
        self.pc_control = None

    def start_server(self, pc_control):
        """Start listening for remote commands"""
        self.pc_control = pc_control
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(('0.0.0.0', self.port))
        server_socket.listen(1)

        print(f"Remote control server listening on port {self.port}")

        while True:
            client, addr = server_socket.accept()
            data = client.recv(1024).decode()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Received from {addr[0]}: {data}")

            if data == "LOCK":
                self.pc_control.lock_pc()
                client.send(b"PC Locked")
            elif data == "SHUTDOWN":
                self.pc_control.shutdown_pc()
                client.send(b"PC Shutting down")
            elif data == "GET_NAME":
                # Send back the computer name
                import platform
                client.send(platform.node().encode())
            elif data == "GET_STATUS":
                # always use your desktop‐switch check
                actual_locked = self.pc_control.check_if_locked()

                # update our state and log
                if actual_locked != self.pc_control.is_locked:
                    self.pc_control.is_locked = actual_locked
                    print(f"[{datetime.now():%H:%M:%S}] Status changed to: {'LOCKED' if actual_locked else 'UNLOCKED'}")

                status = "LOCKED" if actual_locked else "UNLOCKED"
                print(f"[{datetime.now():%H:%M:%S}] Sending status: {status}")
                client.send(status.encode())
            elif data.startswith("MESSAGE:"):
                msg = data.split(":", 1)[1]
                self.pc_control.show_message(msg)
                client.send(b"Message sent")
            elif data.startswith("SET_LIMIT:"):
                try:
                    minutes = int(data.split(":", 1)[1])
                    self.pc_control.set_usage_limit(minutes)
                    client.send(f"Usage limit set to {minutes} minutes".encode())
                except ValueError:
                    client.send(b"Invalid limit value")
            elif data.startswith("ADD_LOCK_TIME:"):
                try:
                    time_str = data.split(":", 1)[1]
                    hour, minute = map(int, time_str.split(":"))
                    self.pc_control.add_scheduled_lock(hour, minute)
                    client.send(f"Lock time added: {hour:02d}:{minute:02d}".encode())
                except ValueError:
                    client.send(b"Invalid value")
            elif data.startswith("EXTEND_TIME:"):
                try:
                    minutes = int(data.split(":", 1)[1])
                    # Add extra time to current limit
                    if self.pc_control.usage_limit:
                        self.pc_control.usage_limit += minutes
                        client.send(f"Extended time by {minutes} minutes".encode())
                    else:
                        client.send(b"No time limit set to extend")
                except ValueError:
                    client.send(b"Invalid time value")

            client.close()

# Main
if __name__ == "main":
    # Create control instance
    control = PCTimeControl()

    # Set up initial time restrictions (optional)
    # control.add_scheduled_lock(21, 0)  # Lock at 9 PM
    # control.add_scheduled_lock(22, 0)  # Lock at 10 PM
    # control.set_usage_limit(120)  # 2 hour limit

    # Start remote control server in separate thread
    remote = RemoteControlServer()
    server_thread = threading.Thread(target=remote.start_server, args=(control,))
    server_thread.daemon = True
    server_thread.start()

    # Run the monitor
    control.run_monitor()