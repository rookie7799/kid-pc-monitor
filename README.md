# Kid PC Monitor

DIY parental control system for parents who code. If you know what 'pip install' means, this is for you!

![Python](https://img.shields.io/badge/python-3.7+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Kids PC](https://img.shields.io/badge/kids_PC-Windows%20%7C%20Linux-lightgrey.svg)
![Web panel](https://img.shields.io/badge/web_panel-Windows%20%7C%20Linux%20%7C%20macOS-green.svg)

## 🎯 Main Functions

- **📱 Use from your phone** - Web interface works on any device
- **🔒 Monitor lock state** - See if kids' computers are locked
- **⏰ Automatic bedtime lock** - Lock computer at set times
- **⏱️ Daily use limits** - Set maximum screen time
- **💬 Send messages** - Display warnings or reminders
- **🏠 Find computers** - Find all computers on your network
- **⏰ Time warnings** - Warn at 15, 5, and 1 minute before lock
- **💾 Save settings** - Settings stay after computer restart
- **👤 User settings** - Control only specific user accounts
- **📊 Live status** - See current limits and time left

## 📸 Screenshots

![Web Interface](screenshots/screenshot_1.png)
![Screenshot 2](screenshots/screenshot_2.png)
![Screenshot 3](screenshots/screenshot_3.png)

## 🚀 Start Now

## ⚠️ What You Need to Know

This is NOT simple to set up. You must do these things:
- Install Python
- Use a terminal / command line
- Know what IP addresses are
- Open firewall ports (Windows on kids' computers; on Linux use `ufw` or your system firewall)
- On kids' computers: Set up a scheduled task (the installer does this)

If these words are not clear to you, use these programs:
- Qustodio
- Net Nanny
- Windows Family Safety

### What You Need

- **Kid Computers:** Windows 10/11 OR Linux (Arch / Ubuntu / Fedora)
- **Parent Computer:** Windows, Linux, or macOS with Python 3.7 or later
- **Network:** Kid computers must accept inbound TCP **9999** from parent computer (usually same local network; can work across subnets if firewall allows)

Auto-discovery finds computers in the same `/24` subnet. If discovery does not find a computer, you can add it by hand.

---

## 📖 Installation Guide

Choose the setup that matches your needs:

### Setup Type A: Parent Computer Separate (Recommended)

Run the control panel on your computer. Your kids cannot get to the control panel. More secure.

#### Step 1: Install on Kid's Computer

**For Windows computers:**

1. Open the terminal as Administrator
2. Go to the folder where you want the program
3. Run these commands:

```bash
git clone https://github.com/Falco20100/kid-pc-monitor.git
cd kid-pc-monitor
python scripts/install.py
```

4. The installer will ask for the kid's Windows user name
5. **IMPORTANT:** Type the kid's user name, not the admin name
6. Answer "yes" for firewall setup

**For Linux computers (Arch / Ubuntu / Fedora):**

1. Open the terminal
2. Go to the folder where you want the program
3. Run these commands:

```bash
git clone https://github.com/Falco20100/kid-pc-monitor.git
cd kid-pc-monitor
chmod +x scripts/install_linux.sh
./scripts/install_linux.sh
```

4. The installer will check for required programs
5. Run these commands to start the monitor:

```bash
systemctl --user enable kid-pc-monitor
systemctl --user start kid-pc-monitor
```

6. For the monitor to start after restart:

```bash
sudo loginctl enable-linger $(whoami)
```

#### Step 2: Install on Parent Computer

**For Windows or macOS:**

1. Go to the folder where you want the program
2. Run these commands:

```bash
git clone https://github.com/Falco20100/kid-pc-monitor.git
cd kid-pc-monitor

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS

pip install -r requirements.txt

cd src
python web_panel.py
```

3. Open your browser and go to: `http://YOUR-COMPUTER-IP:5000`
4. You will see the control panel
5. Click "Scan for PCs" to find kids' computers

**For Linux (Arch / Ubuntu / Fedora):**

1. Go to the folder where you want the program
2. Run these commands:

```bash
git clone https://github.com/Falco20100/kid-pc-monitor.git
cd kid-pc-monitor

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

chmod +x scripts/install_web_panel_linux.sh
./scripts/install_web_panel_linux.sh install
```

3. Run this to start:

```bash
systemctl --user start kid-pc-monitor-web-panel
```

4. Open your browser and go to: `http://YOUR-LINUX-IP:5000`
5. Click "Scan for PCs" to find kids' computers
6. Allow port 5000 in firewall:

```bash
sudo ufw allow 5000/tcp
```

7. For auto-start after restart:

```bash
systemctl --user enable kid-pc-monitor-web-panel
sudo loginctl enable-linger $(whoami)
```

---

### Setup Type B: All on One Computer

Run everything on the kid's computer. Control from your phone on the same network.

**Only for Windows:**

1. Open terminal as Administrator
2. Go to the folder where you want the program
3. Run these commands:

```bash
git clone https://github.com/Falco20100/kid-pc-monitor.git
cd kid-pc-monitor
pip install -r requirements.txt

python scripts/install.py
python scripts/install_web_panel.py
```

4. **IMPORTANT:** When asked for the kid's user name, type the correct user name
5. Answer "yes" for firewall setup
6. Open your browser and go to: `http://KIDS-COMPUTER-IP:5000`

**WARNING:** A smart child can find the control panel at `localhost:5000`. Use Setup Type A for better security.

---

## 📖 How to Use

### Set Daily Use Limit

1. Open the control panel on your phone or browser
2. Click on a kid's computer
3. Look at "Current Settings" section
4. Click a quick button: "30 min", "1 hour", "2 hours"
5. Or enter a custom number of minutes
6. The page updates to show the new limit

### Set Bedtime Lock

1. Select a kid's computer
2. Go to "Set Lock Time"
3. Pick the time (example: 21:00 for 9 PM)
4. Press "Set Bedtime Lock"
5. The computer will lock at that time every day

### Remove Limits

1. Look at "Current Settings"
2. Click the **❌ Clear** button next to the limit you want to remove
3. Or click **Clear All Limits** to remove everything
4. Changes happen right now

### Unlock Computer (Emergency)

You cannot unlock remotely for security. You can:
- Clear the use limit to allow more time
- Clear the lock time to stop automatic locking
- Send a message to the child
- The child enters the password (if one exists)

---

## ⚙️ Setup Options

### Use Different Computer Names

Edit `src/web_panel.py`:

```python
CUSTOM_PC_NAMES = {
    '192.168.1.105': 'Tommy Computer',
    '192.168.1.112': 'Sarah Computer',
}
```

### Monitor Only Some User Accounts

Edit `src/pc_control.py` (Windows) or `src/pc_control_linux.py` (Linux):

```python
# Option 1: Monitor ONLY these users
MONITORED_USERS = ['tommy', 'sarah']
EXEMPT_USERS = []

# Option 2: Monitor everyone EXCEPT these users
MONITORED_USERS = []
EXEMPT_USERS = ['admin', 'parent', 'dad']

# Option 3: Monitor all users (default)
MONITORED_USERS = []
EXEMPT_USERS = []
```

**Example:** If two kids share one computer, you can control only their accounts and let parents use the computer without limits.

### Settings Save Location

Settings save to `pc_control_state.json`:
- Daily use limits
- Lock times
- Start time for tracking

Settings stay after restart. Kids cannot bypass by restarting.

---

## 🔧 Problem Solving

### Check if Monitor is Running (Windows)

The monitor only starts after the kid logs in. The monitor runs in the kid's session.

After the kid logs in, open command prompt and run:

```cmd
netstat -an | findstr 9999
```

You should see: `TCP    0.0.0.0:9999    0.0.0.0:0    LISTENING`

If you do not see this, see "Monitor Does Not Start" below.

### Check if Monitor is Running (Linux)

After the kid logs in, run:

```bash
sudo netstat -tlnp | grep 9999
```

Or:

```bash
ss -tlnp | grep 9999
```

You should see port 9999 listening.

### Computer Shows as "Unknown"

- Add the computer name in configuration
- Check the computer is on the same network
- Check firewall settings
- Wait 30 seconds and refresh the page

### Cannot Connect from Phone

- Check firewall allows port 5000 (control panel computer) and 9999 (each kid computer)
- For Linux control panel: `sudo ufw allow 5000/tcp`
- Use the real IP address, not `localhost`
- Check the control panel program is running

### Monitor Does Not Start (Windows)

- The **wrong user account** is most common: The task runs for the admin user, not the kid user. Open Task Scheduler. Find the **KidPCMonitor** task. Go to **General** tab. Check the user. It should be the kid's user name, not the admin user name.
- To fix: Run `python scripts/install.py` as Administrator again. Enter the correct kid's user name this time.
- Check logs in the `src` folder: `pc_control.log` and `pc_control.out.log`

### Monitor Does Not Start (Linux)

Check the service status:

```bash
systemctl --user status kid-pc-monitor
```

View the logs:

```bash
journalctl --user -u kid-pc-monitor -f
```

Common problems:
- Missing `notify-send`: Install `libnotify`
- Missing screen locker: Install `i3lock` or use your desktop environment's locker
- Wrong file permissions: Run `chmod +x src/pc_control_linux.py`

### Settings Not Saving

- Check file permissions in the `src` folder
- Check that `pc_control_state.json` exists
- Restart the monitor program
- Check logs for errors

---

## 🛡️ Security Notes

- Only works on local network (not the internet)
- No passwords stored
- Computer locked state protected by operating system
- Tech-savvy child can stop the program if they have admin rights
- Use Setup Type A for better security

---

## 🤝 Help and Contribute

Parents and developers welcome!

To contribute:
1. Fork the repository
2. Make a new branch
3. Make your changes
4. Send a pull request

### Recent Changes (v2.0)

- ✅ Time warnings (15, 5, 1 minute before lock)
- ✅ Settings save after restart
- ✅ Control specific user accounts
- ✅ Fixed time calculation
- ✅ Better error messages
- ✅ Show time left in control panel
- ✅ Better system use

### Recent Changes (v2.1)

- ✅ Full Linux agent support (Arch, Ubuntu, Fedora)
- ✅ Linux screen locking (i3lock, GNOME, KDE, Xfce)
- ✅ Desktop notifications on Linux
- ✅ systemd service management
- ✅ Same web control panel for Windows and Linux

### Future Ideas

- Mobile app for iOS and Android
- Use history and reports
- Reward system
- Control specific programs
- Login security
- macOS agent support

---

## 📄 License

MIT License - Change it for your family's needs!

## ❤️ Thanks

Made by parents for parents. Thanks to all people who help make screen time management better!

---

**Need Help?** Open an [issue](https://github.com/Falco20100/kid-pc-monitor/issues)
