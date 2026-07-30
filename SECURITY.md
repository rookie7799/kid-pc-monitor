# Security Policy and Deployment Boundaries

## Current security status

Kid PC Monitor is not safe to expose to an untrusted network.

Known high-impact limitations in the current baseline:

- the Flask panel has no login, authorization, or CSRF protection;
- the panel serves plain HTTP and listens on all interfaces on TCP 5000;
- the Windows agent accepts plain-text commands on TCP 9999 without a token,
  signature, replay protection, or encryption;
- network discovery trusts any host answering on TCP 9999;
- device registration is dynamic rather than an explicit allow-list;
- limits and configuration are local files that an administrator can alter;
- elapsed wall-clock time is counted, including locked and idle time;
- logs can include device addresses, usernames, commands, and operational data.

Consequently, any client that can reach the panel may issue control actions, and
any client that can reach an agent may attempt direct commands. Network firewall
rules are a required compensating control, not an optional hardening step.

## Supported baseline threat model

The first release assumes:

- trusted parents administer Proxmox, the LXC, router, and Windows PCs;
- children use standard Windows accounts without administrator access;
- the home LAN is trusted only where explicit source allow-lists say so;
- no service port is forwarded from the internet;
- remote administration uses an authenticated private VPN;
- physical access, Windows administrator compromise, and a hostile LAN are not
  fully defended by this version.

## Required controls before use

1. Restrict LXC TCP 5000 to fixed/reserved parent device addresses.
2. Restrict every Windows TCP 9999 rule to the fixed/reserved LXC address.
3. Do not publish TCP 5000 or 9999 through the router or a public reverse proxy.
4. Give children standard accounts only; password-protect parent accounts.
5. Prevent child accounts from changing Task Scheduler, firewall configuration,
   the agent code, and its state where Windows permissions permit.
6. Keep a known-good copy of configuration and record the deployed Git commit.
7. Review `pc_control.log` and service logs after failures or unexpected locks.
8. Apply operating-system and Python security updates.

## Secrets

The baseline has no agent secret to store. Do not add passwords, tokens, private
IP inventories, or Proxmox credentials to Git. Future authentication secrets
must come from environment variables or protected host-local files, use a
different secret per device, and never be logged.

## Planned security design

The next security milestone should add, as one coherent protocol change:

- authenticated panel users with password hashing and secure Flask sessions;
- CSRF protection for state-changing browser requests;
- a static registry of approved devices;
- a unique key per agent;
- versioned commands containing device ID, timestamp, nonce, and body;
- HMAC verification with constant-time comparison and a narrow replay window;
- server source-address validation as defense in depth;
- secret rotation and revocation;
- rate limits, audit records, and generic error responses.

HMAC authenticates commands but does not hide them. Continue using a trusted LAN
or VPN; evaluate TLS/mTLS if confidentiality against LAN observers is required.

## Reporting and incident response

Until a private disclosure channel is established, do not publish exploitable
household details, addresses, usernames, or credentials in an issue. If misuse
is suspected:

1. disable the LXC service;
2. disable the `KidPCMonitor` scheduled task on each Windows PC;
3. block TCP 5000 and 9999 at the relevant firewalls;
4. preserve logs and the deployed commit hash;
5. inspect account privileges, firewall rules, scheduled tasks, and changed
   files before restoring service.
