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

import logging
from pathlib import Path

# Set up logging
log_file = 'pc_control.log'
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
                    return f"Usage limit set to {minutes} minutes"
                except ValueError:
                    return "Invalid limit value"
                    
            elif command.startswith("ADD_LOCK_TIME:"):
                try:
                    time_str = command.split(":", 1)[1]
                    hour, minute = map(int, time_str.split(":"))
                    self.pc_control.add_scheduled_lock(hour, minute)
                    return f"Lock time added: {hour:02d}:{minute:02d}"
                except ValueError:
                    return "Invalid time format (use HH:MM)"
                    
            elif command.startswith("EXTEND_TIME:"):
                try:
                    minutes = int(command.split(":", 1)[1])
                    if self.pc_control.usage_limit:
                        self.pc_control.usage_limit += minutes
                        return f"Extended time by {minutes} minutes"
                    return "No time limit set to extend"
                except ValueError:
                    return "Invalid time value"
                    
            elif command == "HELP":
                return (
                    "Available commands:\n"
                    "LOCK - Lock the PC\n"
                    "SHUTDOWN - Shutdown the PC\n"
                    "GET_NAME - Get PC name\n"
                    "GET_STATUS - Check if PC is locked\n"
                    "MESSAGE:<text> - Show popup message\n"
                    "SET_LIMIT:<minutes> - Set usage limit\n"
                    "ADD_LOCK_TIME:HH:MM - Add scheduled lock\n"
                    "EXTEND_TIME:<minutes> - Extend usage time"
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
            except:
                pass
            del self.clients[client_id]
        
        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
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
