from flask import Flask, render_template, request, jsonify, redirect, url_for
import socket
import threading
import ipaddress
import time
from datetime import datetime

app = Flask(__name__)

# Store discovered PCs
discovered_pcs = {}
last_scan_time = None

# Custom PC names (optional) - Add your kids' PC names here
CUSTOM_PC_NAMES = {
    # Example: '192.168.1.105': 'Tommy\'s Laptop',
    # Example: '192.168.1.112': 'Sarah\'s Desktop',
}

def get_local_ip():
    """Get the local IP address of this machine"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def check_pc_status(ip, port=9999):
    """Check if a PC is locked"""
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking status of {ip}")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect((ip, port))
        s.send(b"GET_STATUS")
        status = s.recv(1024).decode().strip()
        s.close()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Status of {ip}: {status}")
        return status
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error checking {ip}: {e}")
        return "UNKNOWN"

def scan_for_servers(port=9999):
    """Scan the local network for PCs running the control server"""
    global discovered_pcs, last_scan_time
    local_ip = get_local_ip()
    network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    discovered_pcs = {}
    
    def check_host(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            result = s.connect_ex((str(ip), port))
            s.close()
            if result == 0:
                # Try to get hostname from the PC directly
                hostname = CUSTOM_PC_NAMES.get(str(ip), None)
                if not hostname:
                    try:
                        # First try to get name from the control server
                        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s2.settimeout(1)
                        s2.connect((str(ip), port))
                        s2.send(b"GET_NAME")
                        hostname = s2.recv(1024).decode().strip()
                        s2.close()
                        if not hostname:
                            raise Exception("Empty name")
                    except:
                        try:
                            # Fallback to system hostname resolution
                            hostname = socket.gethostbyaddr(str(ip))[0]
                            hostname = hostname.split('.')[0].upper()
                        except:
                            hostname = f"PC at {ip}"
                
                discovered_pcs[str(ip)] = {
                    'hostname': hostname,
                    'status': 'online',
                    'locked': False,  # Will update in separate check
                    'last_seen': datetime.now()
                }
        except:
            pass
    
    threads = []
    for ip in network.hosts():
        t = threading.Thread(target=check_host, args=(ip,))
        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()
    
    last_scan_time = datetime.now()
    return discovered_pcs

def send_command(host, command, port=9999):
    """Send a command to the remote PC"""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect((host, port))
        client.send(command.encode())
        response = client.recv(1024)
        client.close()
        return True, response.decode()
    except Exception as e:
        return False, str(e)

@app.route('/')
def index():
    """Main page showing all discovered PCs"""
    # Update lock status for all PCs
    for ip in discovered_pcs:
        status = check_pc_status(ip)
        discovered_pcs[ip]['locked'] = (status == "LOCKED")
    
    return render_template('index.html', 
                         pcs=discovered_pcs, 
                         last_scan=last_scan_time)

@app.route('/scan')
def scan():
    """Scan for PCs and redirect to main page"""
    scan_for_servers()
    return redirect(url_for('index'))

@app.route('/control/<ip>')
def control(ip):
    """Control page for a specific PC"""
    pc_info = discovered_pcs.get(ip, {'hostname': 'Unknown', 'status': 'unknown'})
    # Check current lock status
    status = check_pc_status(ip)
    pc_info['locked'] = (status == "LOCKED")
    
    return render_template('control.html', ip=ip, pc_info=pc_info)

@app.route('/action', methods=['POST'])
def action():
    """Execute an action on a PC"""
    data = request.json
    ip = data.get('ip')
    action_type = data.get('action')
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Action request: {action_type} for {ip}")
    
    if action_type == 'lock':
        success, response = send_command(ip, "LOCK")
        # Update our local status immediately
        if success and ip in discovered_pcs:
            discovered_pcs[ip]['locked'] = True
    elif action_type == 'shutdown':
        success, response = send_command(ip, "SHUTDOWN")
    elif action_type == 'message':
        message = data.get('message', '')
        success, response = send_command(ip, f"MESSAGE:{message}")
    elif action_type == 'set_limit':
        minutes = data.get('minutes', 120)
        success, response = send_command(ip, f"SET_LIMIT:{minutes}")
    elif action_type == 'add_lock_time':
        lock_time = data.get('time', '21:00')
        success, response = send_command(ip, f"ADD_LOCK_TIME:{lock_time}")
    else:
        success, response = False, "Unknown action"
    
    return jsonify({'success': success, 'response': response})

# HTML Templates
INDEX_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kids PC Control Panel</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f0f0f0;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .scan-btn {
            display: block;
            width: 100%;
            padding: 15px;
            margin: 20px 0;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
        }
        .scan-btn:hover {
            background-color: #45a049;
        }
        .pc-card {
            background: white;
            padding: 20px;
            margin: 10px 0;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            cursor: pointer;
            transition: transform 0.2s;
        }
        .pc-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 10px rgba(0,0,0,0.15);
        }
        .pc-name {
            font-size: 18px;
            font-weight: bold;
            color: #333;
        }
        .pc-ip {
            color: #666;
            font-size: 14px;
        }
        .status {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 15px;
            font-size: 12px;
            margin-top: 10px;
        }
        .status.online {
            background-color: #4CAF50;
            color: white;
        }
        .status.locked {
            background-color: #ff9800;
            color: white;
        }
        .last-scan {
            text-align: center;
            color: #666;
            font-size: 14px;
            margin-top: 20px;
        }
    </style>
    <script>
        // Auto-refresh every 30 seconds
        setTimeout(function() {
            location.reload();
        }, 30000);
    </script>
</head>
<body>
    <div class="container">
        <h1>👨‍👩‍👧‍👦 Kids PC Control Panel</h1>
        
        <button onclick="location.href='/scan'" class="scan-btn">
            🔍 Scan for PCs
        </button>
        
        {% if pcs %}
            <h2>Available PCs:</h2>
            {% for ip, info in pcs.items() %}
            <div class="pc-card" onclick="location.href='/control/{{ ip }}'">
                <div class="pc-name">💻 {{ info.hostname }}</div>
                <div class="pc-ip">{{ ip }}</div>
                {% if info.locked %}
                <span class="status locked">🔒 LOCKED</span>
                {% else %}
                <span class="status online">● ONLINE</span>
                {% endif %}
            </div>
            {% endfor %}
        {% else %}
            <p style="text-align: center; color: #666;">
                No PCs found. Click "Scan for PCs" to search.
            </p>
        {% endif %}
        
        {% if last_scan %}
        <div class="last-scan">
            Last scan: {{ last_scan.strftime('%I:%M %p') }}
        </div>
        {% endif %}
    </div>
</body>
</html>
'''

CONTROL_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Control {{ pc_info.hostname }}</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f0f0f0;
        }
        .container {
            max-width: 500px;
            margin: 0 auto;
        }
        h1 {
            color: #333;
            text-align: center;
            font-size: 24px;
        }
        .back-btn {
            display: inline-block;
            padding: 10px 20px;
            background-color: #666;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            margin-bottom: 20px;
        }
        .action-group {
            background: white;
            padding: 20px;
            margin: 15px 0;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .action-title {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 15px;
            color: #333;
        }
        .btn {
            display: block;
            width: 100%;
            padding: 15px;
            margin: 10px 0;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            transition: background-color 0.3s;
        }
        .btn-lock {
            background-color: #ff9800;
            color: white;
        }
        .btn-lock:hover {
            background-color: #e68900;
        }
        .btn-shutdown {
            background-color: #f44336;
            color: white;
        }
        .btn-shutdown:hover {
            background-color: #da190b;
        }
        .btn-message {
            background-color: #2196F3;
            color: white;
        }
        .btn-message:hover {
            background-color: #0b7dda;
        }
        .btn-limit {
            background-color: #9c27b0;
            color: white;
        }
        .btn-limit:hover {
            background-color: #7b1fa2;
        }
        input[type="text"], input[type="number"], input[type="time"] {
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 5px;
            box-sizing: border-box;
            font-size: 16px;
        }
        .quick-limit {
            display: inline-block;
            padding: 8px 15px;
            margin: 5px;
            background-color: #e0e0e0;
            border-radius: 20px;
            cursor: pointer;
            font-size: 14px;
        }
        .quick-limit:hover {
            background-color: #d0d0d0;
        }
        .status-message {
            padding: 15px;
            margin: 15px 0;
            border-radius: 5px;
            text-align: center;
            display: none;
        }
        .status-message.success {
            background-color: #d4edda;
            color: #155724;
        }
        .status-message.error {
            background-color: #f8d7da;
            color: #721c24;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back-btn">← Back to PCs</a>
        
        <h1>💻 {{ pc_info.hostname }}</h1>
        <p style="text-align: center; color: #666;">{{ ip }}</p>
        
        {% if pc_info.locked %}
        <div class="status-message" style="display: block; background-color: #fff3cd; color: #856404;">
            🔒 This computer is currently LOCKED
        </div>
        {% endif %}
        
        <div id="status-message" class="status-message"></div>
        
        <div class="action-group">
            <div class="action-title">🔒 Quick Actions</div>
            <button class="btn btn-lock" onclick="performAction('lock')">
                Lock Computer Now
            </button>
            <button class="btn btn-shutdown" onclick="confirmAndPerform('shutdown')">
                Shutdown Computer
            </button>
        </div>
        
        <div class="action-group">
            <div class="action-title">💬 Send Message</div>
            <input type="text" id="message-text" placeholder="Type your message here...">
            <button class="btn btn-message" onclick="sendMessage()">
                Send Message
            </button>
        </div>
        
        <div class="action-group">
            <div class="action-title">⏱️ Set Time Limit</div>
            <div>Quick limits:</div>
            <div style="text-align: center;">
                <span class="quick-limit" onclick="setQuickLimit(30)">30 min</span>
                <span class="quick-limit" onclick="setQuickLimit(60)">1 hour</span>
                <span class="quick-limit" onclick="setQuickLimit(120)">2 hours</span>
                <span class="quick-limit" onclick="setQuickLimit(180)">3 hours</span>
            </div>
            <input type="number" id="limit-minutes" placeholder="Or enter minutes...">
            <button class="btn btn-limit" onclick="setLimit()">
                Set Time Limit
            </button>
        </div>
        
        <div class="action-group">
            <div class="action-title">🕐 Set Lock Time</div>
            <input type="time" id="lock-time" value="21:00">
            <button class="btn btn-limit" onclick="setLockTime()">
                Set Bedtime Lock
            </button>
        </div>
    </div>
    
    <script>
        function showStatus(message, isSuccess) {
            const statusEl = document.getElementById('status-message');
            statusEl.textContent = message;
            statusEl.className = 'status-message ' + (isSuccess ? 'success' : 'error');
            statusEl.style.display = 'block';
            setTimeout(() => {
                statusEl.style.display = 'none';
            }, 3000);
        }
        
        function performAction(action) {
            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: action
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
                // Reload page after 2 seconds to update lock status
                if (data.success && (action === 'lock' || action === 'shutdown')) {
                    setTimeout(() => {
                        location.reload();
                    }, 2000);
                }
            });
        }
        
        function confirmAndPerform(action) {
            if (confirm('Are you sure you want to shutdown this computer?')) {
                performAction(action);
            }
        }
        
        function sendMessage() {
            const message = document.getElementById('message-text').value;
            if (!message) {
                showStatus('Please enter a message', false);
                return;
            }
            
            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: 'message',
                    message: message
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
                if (data.success) {
                    document.getElementById('message-text').value = '';
                }
            });
        }
        
        function setQuickLimit(minutes) {
            document.getElementById('limit-minutes').value = minutes;
            setLimit();
        }
        
        function setLimit() {
            const minutes = document.getElementById('limit-minutes').value;
            if (!minutes) {
                showStatus('Please enter time in minutes', false);
                return;
            }
            
            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: 'set_limit',
                    minutes: parseInt(minutes)
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
            });
        }
        
        function setLockTime() {
            const time = document.getElementById('lock-time').value;
            if (!time) {
                showStatus('Please select a time', false);
                return;
            }
            
            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: 'add_lock_time',
                    time: time
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
            });
        }
    </script>
</body>
</html>
'''

# Create template files
import os
os.makedirs('templates', exist_ok=True)

with open('templates/index.html', 'w') as f:
    f.write(INDEX_TEMPLATE)

with open('templates/control.html', 'w') as f:
    f.write(CONTROL_TEMPLATE)

if __name__ == '__main__':
    # Do initial scan
    print("Performing initial scan...")
    scan_for_servers()
    
    # Start the web server
    print(f"\nWeb Control Panel starting...")
    print(f"Access from your phone at: http://{get_local_ip()}:5000")
    print(f"Or from this PC at: http://localhost:5000")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
