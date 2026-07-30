# Deploy the Web Panel in a Proxmox LXC

This guide deploys the existing Flask panel in a dedicated Debian 12 or Ubuntu
24.04 LXC. It intentionally keeps the first deployment small: no reverse proxy,
no public DNS, and no internet-facing ports.

Read [SECURITY.md](SECURITY.md) before deployment. The current panel has no
login or CSRF protection. TCP 5000 must therefore be reachable only by trusted
parent devices, and TCP 9999 on each Windows PC only by this LXC.

## Target layout

```text
Parent phone/PC ── TCP 5000 ──> LXC web panel
                                  │
                                  └── TCP 9999 ──> each Windows agent
```

Suggested starting resources:

| Resource | Value |
|---|---|
| OS | Debian 12 or Ubuntu 24.04 |
| CPU | 1 vCPU |
| RAM | 512 MB–1 GB |
| Disk | 8 GB |
| Network | DHCP client; reservation managed on the router |

Network policy for this homelab: keep the LXC configured as a DHCP client.
Permanent addresses and DHCP reservations are managed only on the router. Do
not configure a static address, gateway, or DNS override in the Proxmox guest
configuration or inside the container.

## 1. Create the LXC

Create an unprivileged container in Proxmox, enable start at boot, and configure
its interface with `ip=dhcp`. Do not enable nesting or privileged mode; the
Flask panel does not need either. Use the generated interface MAC address to
create or change the reservation on the router.

Record these deployment values before continuing:

```text
LXC VMID:          <vmid>
LXC IP:            <lxc-ip>
LXC MAC:           <lxc-mac>
Parent device IPs: <parent-ip-1>, <parent-ip-2>
Child PC IPs:      <child-ip-1>, <child-ip-2>
```

## 2. Install the panel

Run inside the LXC as the non-root service user:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv

git clone <your-fork-url> ~/kid-pc-monitor
cd ~/kid-pc-monitor

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verify the application manually first:

```bash
cd ~/kid-pc-monitor/src
../.venv/bin/python web_panel.py
```

From an allowed parent device, open `http://<lxc-ip>:5000`. Stop the manual
process before installing the service.

## 3. Enable the system service

```bash
sudo useradd --system --home-dir /opt/kid-pc-monitor \
  --no-create-home --shell /usr/sbin/nologin kidmonitor
sudo install -d -o kidmonitor -g kidmonitor \
  /opt/kid-pc-monitor/src/templates
sudo install -m 0644 deploy/kid-pc-monitor-web-panel.service \
  /etc/systemd/system/kid-pc-monitor-web-panel.service
sudo systemctl daemon-reload
sudo systemctl enable --now kid-pc-monitor-web-panel.service
```

Useful checks:

```bash
systemctl is-enabled kid-pc-monitor-web-panel.service
systemctl is-active kid-pc-monitor-web-panel.service
journalctl -u kid-pc-monitor-web-panel.service -n 100 --no-pager
ss -ltnp | grep ':5000'
```

Reboot the LXC once and repeat the `is-active` and browser checks.

## 4. Restrict the network

The preferred location for allow-listing is Proxmox Firewall because the rule
then remains outside the guest. Equivalent guest-firewall rules are acceptable.

Required policy:

- allow inbound TCP 5000 only from named parent device IPs;
- deny other inbound TCP 5000 traffic;
- allow the LXC to initiate TCP 9999 to registered child PCs;
- do not add router port forwarding for either port;
- use WireGuard or Tailscale for access away from home, not a public reverse
  proxy to this unauthenticated panel.

Example UFW commands inside the LXC, if UFW is the chosen enforcement point:

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow from <parent-ip-1> to any port 5000 proto tcp
sudo ufw allow from <parent-ip-2> to any port 5000 proto tcp
sudo ufw enable
sudo ufw status numbered
```

Preserve SSH access before enabling a guest firewall. Substitute real IPs; do
not paste placeholders literally.

## 5. Acceptance test

Keep evidence (command output or screenshots) for every checked item:

- [ ] Service is active after an LXC reboot.
- [ ] Panel opens from each allowed parent device.
- [ ] Panel does not open from an unapproved LAN device.
- [ ] Scan finds each child PC.
- [ ] Message and manual lock commands work.
- [ ] A five-minute limit shows 5/1-minute warnings and locks the PC.
- [ ] Unlocking after expiry causes another lock after the retry delay.
- [ ] The limit remains after restarting Windows/the agent.
- [ ] The next calendar day starts a fresh daily window.
- [ ] A parent/exempt account is not restricted.
- [ ] TCP 9999 is reachable from the LXC and blocked from other LAN clients.

Do not declare the baseline deployed until the tests requiring real LXC and
Windows hosts have been observed on those hosts.

## Update and rollback

Before updating, note the working commit and copy the agent state/configuration.
Then fetch the fork, check out the reviewed commit, install requirements, and
restart the service. To roll back, check out the previous known-good commit and
restart the same service. Do not reset or delete `pc_control_state.json` unless
you intentionally want to remove the active limits.
