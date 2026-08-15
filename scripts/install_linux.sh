#!/bin/bash
# Kid PC Monitor - Linux/Arch Installation Script
# Creates systemd user service for the agent

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Kid PC Monitor - Linux Agent Setup${NC}"
echo "====================================="

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}❌ This script should NOT be run as root!${NC}"
   echo "Run it as the user who should be monitored."
   exit 1
fi

# Check if pc_control_linux.py exists
if [ ! -f "src/pc_control_linux.py" ]; then
    echo -e "${RED}❌ Could not find src/pc_control_linux.py${NC}"
    exit 1
fi

# Get the absolute path to the script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/../src/pc_control_linux.py"

echo -e "${GREEN}✅ Found agent: $SCRIPT_PATH${NC}"

# Check for notify-send (required for notifications)
if ! command -v notify-send &> /dev/null; then
    echo -e "${YELLOW}⚠️  notify-send not found. Install libnotify to enable notifications:${NC}"
    echo "   Arch: sudo pacman -S libnotify"
    echo "   Ubuntu/Debian: sudo apt-get install libnotify-bin"
    echo "   Fedora: sudo dnf install libnotify"
fi

# Check for screen locker
echo -e "\n${YELLOW}Checking for screen lock methods...${NC}"
LOCK_FOUND=0
for cmd in i3lock gnome-screensaver-command loginctl xfce4-screensaver-command kdelock; do
    if command -v $cmd &> /dev/null; then
        echo -e "${GREEN}✅ Found: $cmd${NC}"
        LOCK_FOUND=1
    fi
done

if [ $LOCK_FOUND -eq 0 ]; then
    echo -e "${RED}❌ No screen locker found!${NC}"
    echo "Install one of the following:"
    echo "   Arch: sudo pacman -S i3lock"
    echo "   Ubuntu/Debian: sudo apt-get install i3lock"
    echo "   Or use your desktop environment's built-in lock (gnome-screensaver, kdelock, etc)"
    exit 1
fi

# Create systemd user service directory
mkdir -p ~/.config/systemd/user

# Create the systemd service file
echo -e "\n${YELLOW}Creating systemd user service...${NC}"
cat > ~/.config/systemd/user/kid-pc-monitor.service << EOF
[Unit]
Description=Kid PC Monitor - Screen Time Control Agent
After=network.target

[Service]
Type=simple
ExecStart=$SCRIPT_PATH
Restart=on-failure
RestartSec=5
WorkingDirectory=$SCRIPT_DIR/../src
Environment="PATH=/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=default.target
EOF

echo -e "${GREEN}✅ Service file created${NC}"

# Make the agent script executable
chmod +x "$SCRIPT_PATH"
echo -e "${GREEN}✅ Agent script is executable${NC}"

# Show what to do next
echo -e "\n${GREEN}═══════════════════════════════════${NC}"
echo -e "${GREEN}Setup complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════${NC}"

echo -e "\n${YELLOW}To start the agent automatically at login:${NC}"
echo -e "${GREEN}systemctl --user enable kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To start the agent now:${NC}"
echo -e "${GREEN}systemctl --user start kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To check the status:${NC}"
echo -e "${GREEN}systemctl --user status kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To view logs:${NC}"
echo -e "${GREEN}journalctl --user -u kid-pc-monitor -f${NC}"

echo -e "\n${YELLOW}To stop the agent:${NC}"
echo -e "${GREEN}systemctl --user stop kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To disable at login:${NC}"
echo -e "${GREEN}systemctl --user disable kid-pc-monitor${NC}"

echo -e "\n${YELLOW}⚠️  Note: Ensure user lingering is enabled for the agent to start at boot:${NC}"
echo -e "${GREEN}sudo loginctl enable-linger $(whoami)${NC}"
