# Roadmap

The project follows a staged rollout. Large architectural changes wait until the
baseline has been deployed and observed on real Windows and Proxmox hosts.

## Stage 0 — Baseline deployment

- [x] Start automatic limit enforcement in a daemon thread.
- [x] Keep monitoring after the first lock and retry after an unlock.
- [x] Log unexpected monitoring-loop errors.
- [x] Preserve exempt-user behavior.
- [x] Reset daily usage at a calendar-day boundary.
- [x] Add automated tests for elapsed-time, daily reset, and exemption logic.
- [ ] Create and push a personal GitHub fork.
- [ ] Deploy the panel in a dedicated LXC.
- [ ] Install the agent for each child Windows account.
- [ ] Restrict TCP 5000 and 9999 by source address.
- [ ] Complete and record the five-minute acceptance test.

Exit criterion: every item in the acceptance checklist in
[README-DEPLOY.md](README-DEPLOY.md) is evidenced on real hosts.

## Stage 1 — Security and reliability

- [ ] Add panel login, password hashing, secure session settings, and logout.
- [ ] Add CSRF protection to every state-changing route.
- [ ] Replace discovery-as-trust with an explicit device registry.
- [ ] Add a unique secret and identity for each agent.
- [ ] Sign versioned commands with HMAC and reject replays.
- [ ] Load secrets and configuration from protected environment/host files.
- [ ] Add agent/server health timestamps, watchdog behavior, and audit events.
- [ ] Define configuration backup, restore, and secret rotation procedures.

Exit criterion: an unauthorized LAN client cannot use the panel or issue a
valid agent command, and recovery procedures have been tested.

## Stage 2 — Correct active-time accounting

- [ ] Detect keyboard/mouse idle time through Windows APIs.
- [ ] Stop accounting while the workstation is locked.
- [ ] Make the idle threshold configurable (for example, five minutes).
- [ ] Add per-weekday limits and allowed time windows.
- [ ] Add controlled 15/30/60-minute extensions and child requests.
- [ ] Test restarts, sleep/resume, clock changes, midnight, and DST boundaries.

Exit criterion: reports and enforcement match observed active use across the
listed Windows lifecycle cases.

## Stage 3 — Operations and usability

- [ ] Store devices, audit history, and reports in SQLite.
- [ ] Show active, remaining, and last-seen times in a mobile-friendly UI.
- [ ] Alert when an agent is offline or a rule cannot be applied.
- [ ] Keep enforcing the last known policy when the server is unavailable.
- [ ] Add daily/weekly reports and tested configuration backups.

## Stage 4 — Optional extensions

- [ ] Per-application limits and allow/deny rules.
- [ ] Application usage reports.
- [ ] Website filtering through a separate DNS service.
- [ ] HTTPS behind Caddy or Nginx, still reachable only by LAN/VPN.
- [ ] WireGuard or Tailscale remote access.
- [ ] Signed Windows installer and controlled agent updates.
- [ ] Multiple children and devices with explicit roles.

Each optional feature requires its own threat review before implementation.
