import subprocess
import os
import sys
from pathlib import Path

def get_local_users():
    """Get list of local user accounts on this machine."""
    try:
        result = subprocess.run(
            ['powershell', '-Command', 'Get-LocalUser | Select-Object -ExpandProperty Name'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return [u.strip() for u in result.stdout.strip().splitlines() if u.strip()]
    except Exception:
        pass
    return []

def get_target_user():
    """Ask admin which user account to monitor."""
    users = get_local_users()

    print("\n👤 Which user account should be monitored?")

    if users:
        print("\n   Local accounts on this computer:")
        for i, u in enumerate(users, 1):
            print(f"   {i}. {u}")
        print(f"   {len(users)+1}. Enter manually")

        while True:
            choice = input("\nChoice: ").strip()
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(users):
                    return users[idx - 1]
                elif idx == len(users) + 1:
                    break
            print("   Invalid choice, try again.")

    username = input("\n   Enter the username to monitor: ").strip()
    return username if username else None

def get_script_path():
    """Get the path to pc_control.py from user"""
    print("📁 Where is pc_control.py located?")
    print("\nOptions:")
    print("1. Current directory")
    print("2. Same directory as this installer")
    print("3. Enter custom path")

    choice = input("\nChoice (1-3): ").strip()

    if choice == "1":
        script_path = os.path.abspath("pc_control.py")
    elif choice == "2":
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pc_control.py")
    else:
        while True:
            custom_path = input("\nEnter full path to pc_control.py: ").strip()
            custom_path = custom_path.strip('"').strip("'")

            if os.path.exists(custom_path) and custom_path.endswith('.py'):
                script_path = os.path.abspath(custom_path)
                break
            else:
                print("❌ File not found or not a .py file. Please try again.")

    if not os.path.exists(script_path):
        print(f"\n❌ Error: Could not find {script_path}")
        print("Please make sure pc_control.py exists in the specified location.")
        return None

    print(f"\n✅ Found: {script_path}")
    return script_path

def create_task_with_power_settings(target_user):
    """Create scheduled task that runs even on battery power"""

    script_path = get_script_path()
    if not script_path:
        return False

    pythonw_path = sys.executable.replace('python.exe', 'pythonw.exe')
    task_name = f"KidPCMonitor_{target_user}"

    print(f"\n📋 Task Configuration:")
    print(f"   Script: {script_path}")
    print(f"   Python: {pythonw_path}")
    print(f"   Task Name: {task_name}")
    print(f"   Monitoring User: {target_user}")

    confirm = input("\nProceed with these settings? (y/n): ").lower()
    if confirm != 'y':
        print("❌ Setup cancelled.")
        return False

    ps_script = f'''
    $ErrorActionPreference = 'Stop'
    try {{
        $action = New-ScheduledTaskAction -Execute "{pythonw_path}" -Argument "{script_path}" -WorkingDirectory "{os.path.dirname(script_path)}"

        $triggers = @(
            (New-ScheduledTaskTrigger -AtLogon -User "{target_user}")
        )

        $principal = New-ScheduledTaskPrincipal -UserId "{target_user}" -LogonType Interactive -RunLevel Highest

        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -DontStopOnIdleEnd `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0)

        Register-ScheduledTask `
            -TaskName "{task_name}" `
            -Action $action `
            -Trigger $triggers `
            -Principal $principal `
            -Settings $settings `
            -Force

        $task = Get-ScheduledTask -TaskName "{task_name}" -ErrorAction Stop
        Write-Host "SUCCESS: Task verified in Task Scheduler"
        Write-Host "Task Path: $($task.TaskPath)"
        Write-Host "Principal: $($task.Principal.UserId)"
        exit 0
    }}
    catch {{
        Write-Host "ERROR: $_"
        Write-Host "Detailed error: $($_.Exception.Message)"
        exit 1
    }}
    '''

    try:
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-Command', ps_script],
            capture_output=True,
            text=True
        )

        print("\n=== PowerShell Output ===")
        print(result.stdout)
        if result.stderr:
            print("=== Errors ===")
            print(result.stderr)

        if result.returncode == 0:
            verify_cmd = f'schtasks /query /tn "{task_name}"'
            verify_result = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True)

            if verify_result.returncode == 0:
                print("\n✅ Task successfully created and verified!")
                print(f"   - Trigger: At logon for {target_user}")
                print(f"   - Running as: {target_user}")
                print("\nYou can verify in Task Scheduler (taskschd.msc)")
                return True
            else:
                print("\n❌ Task creation failed verification")
                return False
        else:
            print("\n❌ Error creating task")
            if "Access is denied" in result.stderr:
                print("Please ensure you're running as Administrator")
            return False

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return False

def get_existing_monitor_tasks():
    """List all KidPCMonitor_* tasks currently registered."""
    try:
        result = subprocess.run(
            ['powershell', '-Command',
             'Get-ScheduledTask | Where-Object { $_.TaskName -like "KidPCMonitor*" } | Select-Object -ExpandProperty TaskName'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
    except Exception:
        pass
    return []

def remove_task():
    """Remove an existing KidPCMonitor task."""
    tasks = get_existing_monitor_tasks()

    if not tasks:
        print("\nℹ️  No KidPCMonitor tasks found.")
        return

    print("\n🗑️  Which task would you like to remove?")
    for i, t in enumerate(tasks, 1):
        print(f"   {i}. {t}")

    choice = input("\nChoice: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(tasks)):
        print("❌ Invalid choice.")
        return

    task_name = tasks[int(choice) - 1]
    result = subprocess.run(
        f'schtasks /delete /tn "{task_name}" /f',
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        print(f"✅ Task '{task_name}' removed successfully!")
    else:
        print(f"❌ Failed to remove task: {result.stderr}")

def verify_task_settings(task_name):
    """Verify the power settings of a task"""
    query_cmd = f'schtasks /query /tn "{task_name}" /xml'
    result = subprocess.run(query_cmd, shell=True, capture_output=True, text=True)

    if result.returncode == 0:
        xml = result.stdout
        battery_start = "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
        battery_stop = "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml

        print("\n📋 Task Power Settings:")
        print(f"   ✅ Can start on battery: {battery_start}")
        print(f"   ✅ Won't stop on battery: {battery_stop}")

def check_admin():
    """Check if running as administrator"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

if __name__ == "__main__":
    print("Kid PC Monitor - Task Scheduler Setup")
    print("=" * 45)

    if not check_admin():
        print("\n❌ This script needs to run as Administrator!")
        print("   Please right-click and select 'Run as administrator'")
        input("\nPress Enter to exit...")
        sys.exit(1)

    print("\nWhat would you like to do?")
    print("1. Create/Update scheduled task")
    print("2. Remove scheduled task")
    print("3. Exit")

    choice = input("\nChoice (1-3): ").strip()

    if choice == "1":
        target_user = get_target_user()
        if not target_user:
            print("❌ No user selected. Exiting.")
        else:
            print(f"\nSetting up monitoring for user: {target_user}\n")
            if create_task_with_power_settings(target_user):
                print("\n✅ Setup complete!")
            else:
                print("\n❌ Could not create task. Please check the error messages above.")

    elif choice == "2":
        remove_task()

    else:
        print("\nExiting...")

    input("\nPress Enter to close...")
