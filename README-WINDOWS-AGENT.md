# Windows Agent Installation

The agent supports Windows 10/11 and must run in the interactive desktop session
of the child account. Use separate local accounts:

```text
ParentAdmin  local administrator, password protected
Child        standard local user
```

The current agent protocol is unauthenticated. Complete the restricted firewall
step below before relying on it.

## 1. Prepare the checked-out code

Install Python 3 and Git. In an elevated PowerShell or Command Prompt:

```powershell
git clone <your-fork-url> "C:\Program Files\KidPCMonitor"
cd "C:\Program Files\KidPCMonitor"
git rev-parse --short HEAD
```

Use a reviewed commit from the fork, not an unpinned download.

Before installation, edit `src\pc_control.py` and explicitly choose one user
policy. Usernames must match Windows exactly:

```python
MONITORED_USERS = ["Child"]
EXEMPT_USERS = []
```

An alternative is an empty monitored list plus an explicit parent allow-list:

```python
MONITORED_USERS = []
EXEMPT_USERS = ["ParentAdmin"]
```

Prefer the first form when the set of child accounts is known: a newly created
account is unrestricted until intentionally added.

## 2. Install the scheduled task

Run from an administrator terminal:

```powershell
py scripts\install.py
```

When prompted, enter the child's local username. Confirm in Task Scheduler that
`KidPCMonitor` uses that same account and has an at-logon trigger for it.

When the installer offers to open TCP 9999, answer `n`. Its default rule is not
restricted to the LXC address.

## 3. Add a source-restricted firewall rule

Still in elevated PowerShell, remove the installer's broad rule if it exists and
add the restricted rule:

```powershell
Remove-NetFirewallRule -DisplayName "Kid PC Monitor (agent)" -ErrorAction SilentlyContinue

New-NetFirewallRule `
  -DisplayName "Kid PC Monitor from server" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 9999 `
  -RemoteAddress <lxc-ip> `
  -Profile Private
```

Replace `<lxc-ip>` with the real static/reserved LXC address. Verify:

```powershell
Get-NetFirewallRule -DisplayName "Kid PC Monitor*" |
  Get-NetFirewallAddressFilter
```

The resulting `RemoteAddress` must contain only the LXC address, not `Any`.

## 4. Start and inspect the agent

Sign out of the admin account and sign in as the child. The task starts only in
the configured interactive session.

```powershell
Get-ScheduledTask -TaskName KidPCMonitor
Get-ScheduledTaskInfo -TaskName KidPCMonitor
netstat -ano | findstr :9999
```

Expected listener:

```text
0.0.0.0:9999 ... LISTENING
```

The installed code remains protected under `C:\Program Files\KidPCMonitor`.
Agent logs and saved state are writable per-user data under
`%LOCALAPPDATA%\KidPCMonitor` (for example,
`C:\Users\Child\AppData\Local\KidPCMonitor`):

```text
pc_control.log
pc_control.out.log
pc_control_state.json
```

The log must contain both the server start and `PC Time Control is running...`
activity. Do not give the child write permissions to the repository, scheduled
task, or firewall rule if that can be avoided in the local setup.

## 5. Five-minute enforcement test

1. Sign in as the child and verify TCP 9999 is listening.
2. Scan from the web panel and send a harmless message.
3. Set a five-minute daily limit.
4. Confirm the 5-minute and 1-minute warnings appear.
5. Confirm Windows locks when the limit expires.
6. Sign in again without clearing the limit.
7. Confirm Windows locks again after the retry delay (currently 10 seconds).
8. Restart Windows, sign in as the child, and confirm the expired limit still
   applies.
9. Clear the limit from the panel.
10. Sign in as the parent and confirm that no automatic lock occurs.

Also test connectivity from two sources:

- from the LXC, TCP 9999 must connect;
- from another LAN device, TCP 9999 must be refused or time out.

## Removal

Run `py scripts\install.py` as administrator, choose the remove option, and then
remove the source-restricted rule if it remains:

```powershell
Remove-NetFirewallRule -DisplayName "Kid PC Monitor from server" -ErrorAction SilentlyContinue
```

Removing the scheduled task does not automatically make the local repository or
its state disappear. Archive or delete it only after deciding whether the saved
limits and logs are needed.
