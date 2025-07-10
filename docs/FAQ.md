# Frequently Asked Questions

## General Questions

### Is this spyware?
No! This tool:
- Only works on your local network
- Doesn't track browsing history
- Doesn't take screenshots
- Doesn't log keystrokes
- Only manages time limits and lock status

### Can my kids bypass it?
If they have administrator access, yes. This tool is based on trust and communication, not enforcement. For younger kids who don't have admin rights, it's quite effective.

### Does it work on Mac/Linux?
Currently Windows only. Mac/Linux support is planned.

## Setup Issues

### "Python is not recognized as a command"
- Download Python from python.org
- During installation, check "Add Python to PATH"
- Restart your computer

### "Can't connect from my phone"
1. Check both devices are on same WiFi
2. Check Windows Firewall:
   - Open Windows Defender Firewall
   - Click "Allow an app"
   - Add Python to the list
3. Use the IP address shown when starting web_control_panel.py

### "PC shows as Unknown"
This is normal. You can:
1. Add custom names in config.py
2. The PC will still work, just with generic name

## Usage Questions

### Can I unlock a PC remotely?
No, Windows doesn't allow this for security. You can:
- Grant extra time before it locks
- Send a message asking them to save work
- Set up specific "homework time" extensions

### How do I set different limits for different days?
Currently manual, but you can:
- Change limits each day via phone
- Set longer limits on weekends
- Remove limits for special occasions

### What happens if the script crashes?
The PC returns to normal (no restrictions). You can:
- Set it up to restart automatically
- Check logs to see why it crashed
- Kids might notice and restart it (if they're honest!)

## Technical Questions

### How does lock detection work?
We check if LogonUI.exe (Windows lock screen) is running. This is very reliable.

### How can I contribute?
- Report bugs via GitHub issues
- Submit pull requests
- Share your setup tips
- Translate to other languages

### Is this legal to use?
Yes, for your own children on your own computers. Don't use it on computers you don't own or without consent.
