import subprocess
import os
import sys
from pathlib import Path

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
            # Remove quotes if user copied from explorer
            custom_path = custom_path.strip('"').strip("'")
            
            if os.path.exists(custom_path) and custom_path.endswith('.py'):
                script_path = os.path.abspath(custom_path)
                break
            else:
                print("❌ File not found or not a .py file. Please try again.")
    
    # Verify the file exists
    if not os.path.exists(script_path):
        print(f"\n❌ Error: Could not find {script_path}")
        print("Please make sure pc_control.py exists in the specified location.")
        return None
    
    print(f"\n✅ Found: {script_path}")
    return script_path

def create_task_with_power_settings():
    """Create scheduled task that runs even on battery power"""
    
    # Get script path from user
    script_path = get_script_path()
    if not script_path:
        return False
    
    python_path = sys.executable
    task_name = "KidPCMonitor"
    current_user = os.getenv('USERNAME')
    
    # Show what we're about to do
    print(f"\n📋 Task Configuration:")
    print(f"   Script: {script_path}")
    print(f"   Python: {python_path}")
    print(f"   Task Name: {task_name}")
    print(f"   User Account: {current_user}")
    
    confirm = input("\nProceed with these settings? (y/n): ").lower()
    if confirm != 'y':
        print("❌ Setup cancelled.")
        return False
    
    # PowerShell script to create task with specific power settings
    ps_script = f'''
    # Create the action
    $action = New-ScheduledTaskAction -Execute "{python_path}" -Argument "{script_path}" -WorkingDirectory "{os.path.dirname(script_path)}"
    
    # Create multiple triggers
    $triggers = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogon)
    )
    
    # Create principal (run with current user)
    $principal = New-ScheduledTaskPrincipal -UserId "{current_user}" -LogonType InteractiveToken -RunLevel Highest
    
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
    
    Write-Host "Task created successfully under user {current_user}!"
    Write-Host "Triggers: At Startup + At Logon"
    '''
    
    try:
        # Run PowerShell script
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-Command', ps_script],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print("\n✅ Task created with battery power settings!")
            print(f"   - Runs when system starts AND when {current_user} logs in")
            print("   - Will start even on battery power")
            print("   - Won't stop if switching to battery")
            print("   - Will restart if it fails")
            return True
        else:
            print(f"\n❌ Error creating task: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False

def create_task_simple_schtasks():
    """Alternative using schtasks with XML template"""
    
    # Get script path from user
    script_path = get_script_path()
    if not script_path:
        return False
    
    python_path = sys.executable
    task_name = "KidPCMonitor"
    
    print(f"\n📋 Creating task with XML method...")
    
    # Create XML with proper power settings
    xml_content = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Kids PC Time Control - Manages computer usage time</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
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
      <Command>{python_path}</Command>
      <Arguments>"{script_path}"</Arguments>
      <WorkingDirectory>{os.path.dirname(script_path)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>'''
    
    try:
        # Write XML to temp file
        with open('task_config.xml', 'w', encoding='utf-16') as f:
            f.write(xml_content)
        
        # Import the task
        result = subprocess.run(
            f'schtasks /create /tn "{task_name}" /xml "task_config.xml" /f',
            shell=True,
            capture_output=True,
            text=True
        )
        
        # Clean up
        os.remove('task_config.xml')
        
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
    print("Kids PC Time Control - Task Scheduler Setup")
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
        
        # Try PowerShell method first (most reliable)
        if create_task_with_power_settings():
            print("\n✅ Setup complete! Task will run even on laptops using battery.")
        else:
            print("\nTrying alternative method...")
            if create_task_simple_schtasks():
                print("\n✅ Setup complete using XML method!")
            else:
                print("\n❌ Could not create task. Please check the error messages above.")
    
    elif choice == "2":
        remove_task()
    
    else:
        print("\nExiting...")
    
    input("\nPress Enter to close...")