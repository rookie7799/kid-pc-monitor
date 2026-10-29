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

Accept the installer's offer to open TCP 9999: it creates the rule with
`remoteip=LocalSubnet`, so only this LAN can reach the agent. Nothing else is
needed in the common case — section 3 only matters if you want the port open to
a single host instead of the whole subnet.

## 3. Optional: restrict the rule to one panel address

The agent trusts every client that can reach TCP 9999, so the rule must stay
scoped to the LAN subnet (the installer's default) or narrower. To allow only
the panel server, replace the installer's rule with a host-scoped one:

```powershell
Remove-NetFirewallRule -DisplayName "Kid PC Monitor (agent)" -ErrorAction SilentlyContinue

New-NetFirewallRule `
  -DisplayName "Kid PC Monitor from server" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 9999 `
  -RemoteAddress 192.168.0.1 `
  -Profile Private
```

Prefer the subnet-scoped default when the panel's address can change: after a
renumbering a host-scoped rule keeps blocking the panel while the agent itself
still looks healthy — a silent, hard-to-diagnose failure. Verify either form:

```powershell
Get-NetFirewallRule -DisplayName "Kid PC Monitor*" |
  Get-NetFirewallAddressFilter
```

The resulting `RemoteAddress` must be your LAN prefix (or the panel's address)
— never `Any`.

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

Upgrading an older install needs no manual step: on start the agent copies a
`pc_control_state.json` found next to the code (the pre-`%LOCALAPPDATA%`
location) into this folder, logs `Migrated previous settings from ...`, and
leaves the old file in place. Check that line after an upgrade — without the
copy the agent would come up with no limits at all.

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
7. Confirm Windows locks again within a few seconds (the monitor loop polls every 3 seconds).
8. Restart Windows, sign in as the child, and confirm the expired limit still
   applies.
9. Clear the limit from the panel.
10. Sign in as the parent and confirm that no automatic lock occurs.

Also test connectivity from two sources:

- from the panel server, TCP 9999 must connect;
- from another device on the same LAN, TCP 9999 must also connect (the rule is
  subnet-scoped by default);
- from outside the LAN — for example a phone on mobile data — TCP 9999 must be
  refused or time out.

## Removal

Run `py scripts\install.py` as administrator and choose the remove option; it
deletes the agent's own firewall rule too. Remove the extra rule only if you
narrowed it to a single address in section 3:

```powershell
Remove-NetFirewallRule -DisplayName "Kid PC Monitor from server" -ErrorAction SilentlyContinue
```

Removing the scheduled task does not touch the program folder
(`C:\Program Files\KidPCMonitor`) or the per-user data folder
(`%LOCALAPPDATA%\KidPCMonitor`, which still holds `pc_control_state.json` and
the logs). Delete them only after deciding whether the saved limits and logs are
needed.
