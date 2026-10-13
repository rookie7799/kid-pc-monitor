#!/bin/bash
# Kid PC Monitor - Linux/Arch Installation Script
# Creates systemd user service for the agent

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Kid PC Monitor - Linux Agent Setup${NC}"
echo "====================================="

if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}✘ This script should NOT be run as root!${NC}"
   echo "Run it as the user who should be monitored."
   exit 1
fi

if [ ! -f "src/pc_control_linux.py" ]; then
    echo -e "${RED}✘ Could not find src/pc_control_linux.py${NC}"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/../src/pc_control_linux.py"

echo -e "${GREEN}✓ Found agent: $SCRIPT_PATH${NC}"

if ! command -v notify-send &> /dev/null; then
    echo -e "${YELLOW}⚠ notify-send not found. Install libnotify:${NC}"
    echo "   Arch: sudo pacman -S libnotify"
    echo "   Ubuntu/Debian: sudo apt-get install libnotify-bin"
    echo "   Fedora: sudo dnf install libnotify"
fi

echo -e "\n${YELLOW}Checking for screen lock methods...${NC}"
LOCK_FOUND=0
for cmd in i3lock gnome-screensaver-command loginctl xfce4-screensaver-command kdelock; do
    if command -v $cmd &> /dev/null; then
        echo -e "${GREEN}✓ Found: $cmd${NC}"
        LOCK_FOUND=1
    fi
done

if [ $LOCK_FOUND -eq 0 ]; then
    echo -e "${RED}✘ No screen locker found!${NC}"
    echo "Install one: sudo pacman -S i3lock"
    exit 1
fi

mkdir -p ~/.config/systemd/user

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

[Install]
WantedBy=default.target
EOF

echo -e "${GREEN}✓ Service file created${NC}"
chmod +x "$SCRIPT_PATH"
echo -e "${GREEN}✓ Agent script is executable${NC}"

echo -e "\n${GREEN}═════════════════════════════════════${NC}"
echo -e "${GREEN}Setup complete!${NC}"
echo -e "${GREEN}═════════════════════════════════════${NC}"

echo -e "\n${YELLOW}To start the agent automatically at login:${NC}"
echo -e "${GREEN}systemctl --user enable kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To start now:${NC}"
echo -e "${GREEN}systemctl --user start kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To check status:${NC}"
echo -e "${GREEN}systemctl --user status kid-pc-monitor${NC}"

echo -e "\n${YELLOW}To view logs:${NC}"
echo -e "${GREEN}journalctl --user -u kid-pc-monitor -f${NC}"

echo -e "\n${YELLOW}To stop:${NC}"
echo -e "${GREEN}systemctl --user stop kid-pc-monitor${NC}"

echo -e "\n${YELLOW}⚠ Enable user lingering for boot startup:${NC}"
echo -e "${GREEN}sudo loginctl enable-linger $(whoami)${NC}"
