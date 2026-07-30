import subprocess
import os
import sys
from pathlib import Path

# Port the pc_control agent listens on for the parent web panel to connect to.
# Must match RemoteControlServer's default port in src/pc_control.py.
AGENT_PORT = 9999
FIREWALL_RULE_NAME = "Kid PC Monitor (agent)"


def quote_windows_argument(value):
    """Quote one command-line argument using Windows parsing rules."""
    return subprocess.list2cmdline([str(value)])


def quote_powershell_literal(value):
    """Return a single-quoted PowerShell literal."""
    return "'" + str(value).replace("'", "''") + "'"

def find_pc_control():
    """Locate pc_control.py relative to this installer.

    The repo layout is fixed: this script lives in scripts/ and pc_control.py
    lives in src/, so we can find it without asking the user. We also check a
    couple of fallback locations in case the files were moved.
    """
    installer_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(installer_dir, "..", "src", "pc_control.py"),  # repo layout
        os.path.join(installer_dir, "pc_control.py"),               # alongside installer
        os.path.join(os.getcwd(), "pc_control.py"),                 # current directory
    ]
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate):
            return candidate
    return None

def get_script_path():
    """Get the path to pc_control.py, auto-detecting when possible."""
    script_path = find_pc_control()

    if script_path:
        print(f"✅ Found pc_control.py: {script_path}")
        return script_path

    # Auto-detection failed — fall back to asking the user.
    print("⚠️  Could not auto-detect pc_control.py.")
    while True:
        custom_path = input("\nEnter full path to pc_control.py: ").strip()
        # Remove quotes if user copied from explorer
        custom_path = custom_path.strip('"').strip("'")

        if os.path.exists(custom_path) and custom_path.endswith('.py'):
            return os.path.abspath(custom_path)
        else:
            print("❌ File not found or not a .py file. Please try again.")

def get_target_user():
    """Ask which user account the monitor should run under."""
    elevated_user = os.getenv('USERNAME') or ''
    print("\n👤 Which Windows account should be monitored?")
    print(f"   This is the account the kid logs in with — NOT the admin")
    print(f"   account running this installer (currently '{elevated_user}').")
    while True:
        target = input("\nKid's Windows username: ").strip()
        if not target:
            if not elevated_user:
                print("❌ No username entered and USERNAME env var is not set. Please type the account name.")
                continue
            print(f"⚠️  No username entered; falling back to '{elevated_user}'.")
            target = elevated_user
        result = subprocess.run(['net', 'user', target], capture_output=True, text=True)
        if result.returncode == 0:
            return target
        print(f"❌ Account '{target}' not found on this PC. Check the spelling and try again.")
        print(f"   (Run 'net user' in a command prompt to list local accounts.)")

def create_task_with_power_settings(script_path, target_user):
    """Create scheduled task that runs even on battery power"""
    pythonw_path = str(Path(sys.executable).parent / 'pythonw.exe')
    working_directory = os.path.dirname(script_path)
    script_argument = quote_windows_argument(script_path)
    task_name = "KidPCMonitor"

    # Show what we're about to do
    print(f"\n📋 Task Configuration:")
    print(f"   Script: {script_path}")
    print(f"   Python: {pythonw_path}")
    print(f"   Task Name: {task_name}")
    print(f"   Monitored account: {target_user}")

    confirm = input("\nProceed with these settings? (y/n): ").lower()
    if confirm != 'y':
        print("❌ Setup cancelled.")
        return False

    # PowerShell script to create task with specific power settings
    ps_script = f'''
    $ErrorActionPreference = 'Stop'
    try {{
        # Create the action
        $action = New-ScheduledTaskAction `
            -Execute {quote_powershell_literal(pythonw_path)} `
            -Argument {quote_powershell_literal(script_argument)} `
            -WorkingDirectory {quote_powershell_literal(working_directory)}

        # Trigger when the monitored user logs on (scoped to that account so it
        # fires for the kid's session, not the admin's). An AtStartup trigger is
        # useless here because an interactive token only exists after logon.
        $triggers = @(
            (New-ScheduledTaskTrigger -AtLogon -User "{target_user}")
        )

        # Run as the monitored (kid) account. It's a standard user, so use the
        # Limited run level — Highest would request an elevation it can't grant
        # and can stop the task from starting.
        $principal = New-ScheduledTaskPrincipal -UserId "{target_user}" -LogonType Interactive -RunLevel Limited
        
        # Create settings with power options
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -DontStopOnIdleEnd `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0)
        
        # Register the task
        Register-ScheduledTask `
            -TaskName "{task_name}" `
            -Action $action `
            -Trigger $triggers `
            -Principal $principal `
            -Settings $settings `
            -Force
        
        # Verify task was actually created
        $task = Get-ScheduledTask -TaskName "{task_name}" -ErrorAction Stop
        Write-Host "SUCCESS: Task verified in Task Scheduler"
        Write-Host "Task Path: $($task.TaskPath)"
        Write-Host "Triggers: $($task.Triggers)"
        Write-Host "Principal: $($task.Principal)"
        exit 0
    }}
    catch {{
        Write-Host "ERROR: $_"
        Write-Host "Detailed error: $($_.Exception.Message)"
        exit 1
    }}
    '''
    
    try:
        # Run PowerShell script
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-Command', ps_script],
            capture_output=True,
            text=True
        )
        
        # Debug output
        print("\n=== PowerShell Output ===")
        print(result.stdout)
        if result.stderr:
            print("=== Errors ===")
            print(result.stderr)
        
        if result.returncode == 0:
            # Additional verification
            verify_cmd = f'schtasks /query /tn "{task_name}"'
            verify_result = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True)
            
            if verify_result.returncode == 0:
                print("\n✅ Task successfully created and verified!")
                print(f"   - Trigger: At logon of {target_user}")
                print(f"   - Running as: {target_user}")
                print("\nYou can verify in Task Scheduler (taskschd.msc)")
                return True
            else:
                print("\n❌ Task creation failed verification")
                print("Try running this script as Administrator again")
                return False
        else:
            print("\n❌ Error creating task")
            if "Access is denied" in result.stderr:
                print("Please ensure you're running as Administrator")
            return False
            
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return False
    
def create_task_simple_schtasks(script_path, target_user):
    """Alternative using schtasks with XML template"""
    pythonw_path = str(Path(sys.executable).parent / 'pythonw.exe')
    task_name = "KidPCMonitor"

    print(f"\n📋 Creating task with XML method...")

    # Create XML with proper power settings. The trigger and principal are both
    # scoped to the monitored (kid) account, and run at the Limited level since
    # it's a standard, non-elevated user.
    xml_content = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Kid PC Monitor - Manages computer usage time</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{target_user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{target_user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw_path}</Command>
      <Arguments>"{script_path}"</Arguments>
      <WorkingDirectory>{os.path.dirname(script_path)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>'''
    
    xml_path = os.path.join(os.path.dirname(script_path), 'task_config.xml')
    try:
        try:
            with open(xml_path, 'w', encoding='utf-16') as f:
                f.write(xml_content)
            result = subprocess.run(
                f'schtasks /create /tn "{task_name}" /xml "{xml_path}" /f',
                shell=True, capture_output=True, text=True
            )
        finally:
            if os.path.exists(xml_path):
                os.remove(xml_path)

        if result.returncode == 0:
            print("\n✅ Task created successfully with battery settings!")
            verify_task_settings(task_name)
            return True
        else:
            print(f"\n❌ Error: {result.stderr}")
            return False

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False

def verify_task_settings(task_name):
    """Verify the power settings of a task"""
    
    # Query task and check settings
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

def print_logon_start_hint(target_user):
    """Explain when the agent actually starts.

    The task runs with an Interactive logon type and the agent needs the kid's
    desktop session (it shows dialogs, locks the workstation and watches the
    foreground window). It therefore can't run while only the admin is logged
    in — it starts on the kid's next logon via the AtLogon trigger. Checking
    port 9999 right after install would show nothing, which is expected.
    """
    print("\n" + "=" * 45)
    print(f"💡 The agent starts automatically when '{target_user}' next logs in.")
    print(f"   It runs inside that account's desktop session, so it won't be")
    print(f"   running yet while you're signed in as the admin — that's normal.")
    print(f"   Log in as '{target_user}' to start it (or, if that account is")
    print(f"   already signed in, run: schtasks /run /tn \"KidPCMonitor\").")
    print("=" * 45)

def manual_firewall_command():
    """The netsh command a user can run by hand to open the agent port."""
    return (
        f'netsh advfirewall firewall add rule '
        f'name="{FIREWALL_RULE_NAME}" dir=in action=allow '
        f'protocol=TCP localport={AGENT_PORT}'
    )

def configure_firewall():
    """Add a Windows Firewall inbound rule for the agent port.

    The agent listens on AGENT_PORT so the parent web panel can connect to it.
    Without an inbound rule Windows Firewall blocks those connections, so the
    panel can't reach the kid PC. We make this idempotent by deleting any rule
    with the same name first, then adding a fresh one.
    """
    print(f"\n🔥 Windows Firewall: the agent listens on TCP port {AGENT_PORT} so")
    print("   the parent web panel can connect to this PC.")
    print("   Incoming connections must be allowed through the firewall.")

    confirm = input(
        f"\nAdd a firewall rule to allow incoming TCP port {AGENT_PORT}? (y/n): "
    ).lower()
    if confirm != 'y':
        print("\nℹ️  Skipped. To allow it later, run this in an admin prompt:")
        print(f"   {manual_firewall_command()}")
        return False

    # Remove any pre-existing rule with this name so we don't stack duplicates.
    subprocess.run(
        f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}"',
        shell=True, capture_output=True, text=True
    )

    result = subprocess.run(
        manual_firewall_command(), shell=True, capture_output=True, text=True
    )

    if result.returncode == 0:
        print(f"\n✅ Firewall rule added: incoming TCP port {AGENT_PORT} allowed.")
        return True
    else:
        print("\n❌ Could not add firewall rule automatically.")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        print("\nYou can add it manually from an admin prompt:")
        print(f"   {manual_firewall_command()}")
        return False

def remove_firewall_rule():
    """Remove the agent firewall rule (used when removing the task)."""
    result = subprocess.run(
        f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}"',
        shell=True, capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"✅ Firewall rule '{FIREWALL_RULE_NAME}' removed.")
    else:
        print("ℹ️  No matching firewall rule found.")

def remove_task():
    """Remove existing task"""
    task_name = "KidPCMonitor"
    print(f"\n🗑️  Removing task '{task_name}'...")
    
    result = subprocess.run(
        f'schtasks /delete /tn "{task_name}" /f',
        shell=True,
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print("✅ Task removed successfully!")
    else:
        print("ℹ️  Task not found or already removed.")
    
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
        print("\nCreating scheduled task with battery-friendly settings...\n")
        script_path = get_script_path()
        target_user = get_target_user()

        # Try PowerShell method first (most reliable)
        task_created = create_task_with_power_settings(script_path, target_user)
        if task_created:
            print("\n✅ Setup complete! Task will run even on laptops using battery.")
        else:
            print("\nTrying alternative method...")
            task_created = create_task_simple_schtasks(script_path, target_user)
            if task_created:
                print("\n✅ Setup complete using XML method!")
            else:
                print("\n❌ Could not create task. Please check the error messages above.")

        # The task only matters if the parent web panel can reach the agent,
        # which Windows Firewall blocks by default — offer to open the port.
        if task_created:
            configure_firewall()
            print_logon_start_hint(target_user)

    elif choice == "2":
        remove_task()
        remove_firewall_rule()
    
    else:
        print("\nExiting...")
    
    input("\nPress Enter to close...")
