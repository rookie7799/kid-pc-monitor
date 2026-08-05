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

# Per-PC status cache with TTL to avoid a TCP round-trip on every page render.
# Cached values (status, current_user, usage_limit, time_remaining,
# allowed_window, unlock_status) are refreshed only after STATUS_CACHE_TTL
# seconds, or on-demand by /scan. A PC that stops answering within the TTL
# shows stale-but-fast data until the next refresh.
STATUS_CACHE_TTL = 10.0
status_cache = {}  # ip -> { 'values': {...}, 'ts': monotonic_ts }
_cache_lock = threading.Lock()

def _cache_get(ip):
    entry = status_cache.get(ip)
    if entry and (time.monotonic() - entry['ts']) < STATUS_CACHE_TTL:
        return entry['values']
    return None

def _cache_set(ip, values):
    with _cache_lock:
        status_cache[ip] = {'values': values, 'ts': time.monotonic()}

def _refresh_pc_details(ip, port=9999):
    """Fetch all per-PC details in parallel and store them in the cache.

    Returns the cached values dict. Each field is fetched independently and
    missing/errored fields fall back to the previous cached value (or None).
    """
    results = {}

    def fetch(key, fn):
        try:
            results[key] = fn(ip, port)
        except Exception:
            results[key] = None

    threads = [
        threading.Thread(target=fetch, args=('status', check_pc_status)),
        threading.Thread(target=fetch, args=('current_user', get_current_user)),
        threading.Thread(target=fetch, args=('usage_limit', get_usage_limit)),
        threading.Thread(target=fetch, args=('time_remaining', get_time_remaining)),
        threading.Thread(target=fetch, args=('allowed_window', get_allowed_window)),
        threading.Thread(target=fetch, args=('unlock_status', get_unlock_status)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Merge onto the previous cached values so transient errors never blank a PC.
    prev = status_cache.get(ip, {}).get('values', {}) if ip in status_cache else {}
    merged = dict(prev)
    for k, v in results.items():
        if v is not None:
            merged[k] = v
    _cache_set(ip, merged)
    return merged

def _pc_details(ip, port=9999):
    """Return per-PC details, refreshing the cache only if stale/absent."""
    cached = _cache_get(ip)
    if cached is not None:
        return cached
    # Guard against stampede: only one thread refreshes, others reuse whatever
    # becomes available.
    with _cache_lock:
        if (ip in status_cache
                and (time.monotonic() - status_cache[ip]['ts']) < STATUS_CACHE_TTL):
            return status_cache[ip]['values']
    return _refresh_pc_details(ip, port)

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
        s.settimeout(0.7)
        s.connect((ip, port))
        s.send(b"GET_STATUS")
        status = s.recv(1024).decode().strip()
        s.close()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Status of {ip}: {status}")
        return status
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error checking {ip}: {e}")
        return "UNKNOWN"

def get_current_user(ip, port=9999):
    """Get the current username logged in on the kid PC (as reported by the agent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.7)
        s.connect((ip, port))
        s.send(b"GET_CURRENT_USER")
        username = s.recv(1024).decode().strip()
        s.close()
        return username
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error getting user from {ip}: {e}")
        return None

def get_usage_limit(ip, port=9999):
    """Get the current usage limit in minutes"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.7)
        s.connect((ip, port))
        s.send(b"GET_USAGE_LIMIT")
        limit = s.recv(1024).decode().strip()
        s.close()
        return None if limit == "None" else int(limit)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error getting limit from {ip}: {e}")
        return None

def get_time_remaining(ip, port=9999):
    """Get time remaining until next lock"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.7)
        s.connect((ip, port))
        s.send(b"GET_TIME_REMAINING")
        remaining = s.recv(1024).decode().strip()
        s.close()
        return remaining
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error getting time remaining from {ip}: {e}")
        return None

def get_allowed_window(ip, port=9999):
    """Get the allowed usage window (e.g. '07:00-22:00')"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.7)
        s.connect((ip, port))
        s.send(b"GET_ALLOWED_WINDOW")
        window = s.recv(1024).decode().strip()
        s.close()
        return window
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error getting allowed window from {ip}: {e}")
        return None

def get_unlock_status(ip, port=9999):
    """Get active temporary-unlock status (e.g. 'ACTIVE 25 minutes' or 'INACTIVE')"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.7)
        s.connect((ip, port))
        s.send(b"GET_UNLOCK_STATUS")
        status = s.recv(1024).decode().strip()
        s.close()
        return status
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error getting unlock status from {ip}: {e}")
        return None

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
                # A fresh scan may reveal new state; drop this PC's stale cache.
                with _cache_lock:
                    status_cache.pop(str(ip), None)
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
    # Pull per-PC details from the TTL cache (refreshed in parallel only when
    # stale) instead of blocking on a fresh TCP round-trip per field per load.
    for ip in discovered_pcs:
        details = _pc_details(ip)
        discovered_pcs[ip]['locked'] = (details.get('status') == "LOCKED")
        if details.get('current_user'):
            discovered_pcs[ip]['current_user'] = details['current_user']
        discovered_pcs[ip]['usage_limit'] = details.get('usage_limit')
        discovered_pcs[ip]['time_remaining'] = details.get('time_remaining')

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
    # Fetch all per-PC details via the TTL cache (parallel refresh when stale).
    details = _pc_details(ip)
    pc_info['locked'] = (details.get('status') == "LOCKED")
    if details.get('current_user'):
        pc_info['current_user'] = details['current_user']
    # Update even if None to clear old values.
    pc_info['usage_limit'] = details.get('usage_limit')
    pc_info['time_remaining'] = details.get('time_remaining')
    pc_info['allowed_window'] = details.get('allowed_window')
    pc_info['unlock_status'] = details.get('unlock_status')

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
    elif action_type == 'set_allowed_window':
        window = data.get('window', '07:00-22:00')
        success, response = send_command(ip, f"SET_ALLOWED_WINDOW:{window}")
    elif action_type == 'unlock':
        minutes = data.get('minutes', 30)
        success, response = send_command(ip, f"UNLOCK:{minutes}")
    elif action_type == 'cancel_unlock':
        success, response = send_command(ip, "CANCEL_UNLOCK")
    elif action_type == 'clear_usage_limit':
        success, response = send_command(ip, "CLEAR_USAGE_LIMIT")
    elif action_type == 'clear_all':
        success, response = send_command(ip, "CLEAR_ALL")
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
        .pc-limit {
            color: #9c27b0;
            font-size: 14px;
            margin-top: 4px;
        }
        .pc-remaining {
            color: #2196F3;
            font-size: 14px;
            font-weight: bold;
            margin-top: 4px;
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
                {% if info.get('current_user') %}
                <div class="pc-ip">👤 User: {{ info.current_user }}</div>
                {% endif %}
                {% if info.get('usage_limit') %}
                <div class="pc-limit">⏱️ Limit: {{ info.usage_limit }} min ({{ (info.usage_limit / 60)|round(1) }}h)</div>
                {% endif %}
                {% if info.get('time_remaining') and info.get('time_remaining') != 'No limits set' %}
                <div class="pc-remaining">⏳ Remaining: {{ info.time_remaining }}</div>
                {% endif %}
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
        {% if pc_info.get('current_user') %}
        <p style="text-align: center; color: #666;">👤 User: <strong>{{ pc_info.current_user }}</strong></p>
        {% endif %}

        <!-- Display Current Settings (Always Visible) -->
        <div class="action-group">
            <div class="action-title">📊 Current Settings</div>

            <!-- Daily Usage Limit -->
            <p>⏱️ <strong>Daily Limit:</strong>
            {% if pc_info.get('usage_limit') %}
                {{ pc_info.usage_limit }} minutes ({{ (pc_info.usage_limit / 60)|round(1) }} hours)
                <button onclick="clearLimit('usage')" style="margin-left: 10px; padding: 5px 10px; background-color: #f44336; color: white; border: none; border-radius: 3px; cursor: pointer; font-size: 12px;">❌ Clear</button>
            {% else %}
                <span style="color: #999;">Not set</span>
            {% endif %}
            </p>

            <!-- Time Remaining -->
            {% if pc_info.get('time_remaining') and pc_info.get('time_remaining') != 'No limits set' %}
            <p>⏳ <strong>Time Remaining:</strong> {{ pc_info.time_remaining }}</p>
            {% endif %}

            <!-- Allowed Window -->
            <p>🕒 <strong>Allowed Window:</strong>
            {% if pc_info.get('allowed_window') %}
                {{ pc_info.allowed_window }}
            {% else %}
                <span style="color: #999;">07:00-22:00</span>
            {% endif %}
            </p>

            <!-- Temporary Unlock Status -->
            {% if pc_info.get('unlock_status') and pc_info.get('unlock_status').startswith('ACTIVE') %}
            <p>🔓 <strong>Temporary Unlock:</strong> <span style="color: #4CAF50; font-weight: bold;">{{ pc_info.unlock_status }}</span>
                <button onclick="cancelUnlock()" style="margin-left: 10px; padding: 5px 10px; background-color: #f44336; color: white; border: none; border-radius: 3px; cursor: pointer; font-size: 12px;">❌ Cancel</button>
            </p>
            {% endif %}

            <!-- Clear All Button -->
            {% if pc_info.get('usage_limit') %}
            <button onclick="clearLimit('all')" style="width: 100%; margin-top: 10px; padding: 10px; background-color: #ff5722; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 14px;">🗑️ Clear All Limits</button>
            {% endif %}
        </div>

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
            <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px;">
                <input type="number" id="unlock-minutes" value="30" min="5" max="240" style="flex: 1;" title="Unlock duration in minutes">
                <button class="btn btn-lock" onclick="performUnlock()" style="flex: 1; background-color: #4CAF50;">
                    🔓 Unlock
                </button>
            </div>
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
            <div class="action-title">🕒 Set Allowed Window</div>
            <div style="color: #666; font-size: 14px; margin-bottom: 5px;">Allowed usage hours (PC locks outside this window):</div>
            <div style="display: flex; align-items: center; gap: 10px;">
                <input type="time" id="allowed-start" value="07:00" style="flex: 1;">
                <span>to</span>
                <input type="time" id="allowed-end" value="22:00" style="flex: 1;">
            </div>
            <button class="btn btn-limit" onclick="setAllowedWindow()">
                Set Allowed Window
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
                if (data.success) {
                    // Reload page after 1 second to show updated settings
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                }
            });
        }
        
        function performUnlock() {
            const minutes = document.getElementById('unlock-minutes').value;
            if (!minutes || parseInt(minutes) < 1) {
                showStatus('Please enter a valid unlock duration (minutes)', false);
                return;
            }
            if (!confirm(`Grant temporary unlock for ${minutes} minutes? (Daily limit still applies)`)) {
                return;
            }
            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: 'unlock',
                    minutes: parseInt(minutes)
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
                if (data.success) {
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                }
            });
        }

        function cancelUnlock() {
            if (!confirm('Cancel the current temporary unlock?')) {
                return;
            }
            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: 'cancel_unlock'
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
                if (data.success) {
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                }
            });
        }
        
        function setAllowedWindow() {
            const start = document.getElementById('allowed-start').value;
            const end = document.getElementById('allowed-end').value;
            if (!start || !end) {
                showStatus('Please select both start and end times', false);
                return;
            }

            fetch('/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ip: '{{ ip }}',
                    action: 'set_allowed_window',
                    window: start + '-' + end
                })
            })
            .then(response => response.json())
            .then(data => {
                showStatus(data.response, data.success);
                if (data.success) {
                    // Reload page after 1 second to show updated settings
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                }
            });
        }

        function clearLimit(type) {
            let confirmMsg, action;

            if (type === 'usage') {
                confirmMsg = 'Clear the daily usage limit?';
                action = 'clear_usage_limit';
            } else if (type === 'all') {
                confirmMsg = 'Clear ALL limits?';
                action = 'clear_all';
            }

            if (!confirm(confirmMsg)) {
                return;
            }

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
                if (data.success) {
                    // Reload page after 1 second to show updated settings
                    setTimeout(() => {
                        location.reload();
                    }, 1000);
                }
            });
        }
    </script>
</body>
</html>
'''

# Create template files
import os
os.makedirs('templates', exist_ok=True)

with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(INDEX_TEMPLATE)

with open('templates/control.html', 'w', encoding='utf-8') as f:
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
