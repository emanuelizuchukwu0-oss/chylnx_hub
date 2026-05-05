from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import secrets
import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
from functools import lru_cache
import time

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# Database setup
DB_PATH = '/tmp/chylnx.db'

# Simple cache for settings (5 minute TTL)
settings_cache = {}
settings_cache_time = 0
CACHE_TTL = 300  # 5 seconds (faster updates)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        payment_verified INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        bank_name TEXT NOT NULL,
        reference TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_name TEXT NOT NULL,
        message_text TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_system INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        setting_key TEXT UNIQUE NOT NULL,
        setting_value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Indexes for speed
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_payment ON users(payment_verified)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp DESC)")
    
    # Create admin
    c.execute("SELECT * FROM users WHERE email = 'admin@chylnx.com'")
    if not c.fetchone():
        c.execute("INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?, ?, ?, ?, ?)",
                   ('Administrator', 'admin@chylnx.com', hash_password('admin123'), 1, 1))
    
    # Settings
    settings = [
        ('game_timer_hours', '24'),
        ('game_timer_minutes', '0'),
        ('game_timer_seconds', '0'),
        ('weekly_timer_days', '7'),
        ('weekly_timer_hours', '0'),
        ('weekly_timer_minutes', '0'),
        ('weekly_timer_seconds', '0'),
        ('info_bar_text', 'Welcome to Chylnx Hub! 🎮 Join our community chat!'),
        ('info_bar_color', '#667eea')
    ]
    
    for key, value in settings:
        c.execute("INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
    
    conn.commit()
    conn.close()
    print("✅ Database ready")

init_db()

# Cached settings getter
def get_cached_settings():
    global settings_cache, settings_cache_time
    now = time.time()
    if now - settings_cache_time > CACHE_TTL or not settings_cache:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
        settings_cache = {row['setting_key']: row['setting_value'] for row in rows}
        conn.close()
        settings_cache_time = now
    return settings_cache

def get_user_fast(email):
    """Optimized user fetch with single query"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute(
        "SELECT id, full_name, email, password_hash, display_name, payment_verified, is_admin FROM users WHERE email = ?", 
        (email.lower(),)
    ).fetchone()
    conn.close()
    return dict(user) if user else None

# Serve HTML files
@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:filename>')
def serve_file(filename):
    return send_from_directory('.', filename)

# API Routes
@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.json
        full_name = data.get('fullName', '').strip()
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields required'}), 400
        if len(password) < 6:
            return jsonify({'error': 'Password too short'}), 400
        
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
                        (full_name, email, hash_password(password)))
            conn.commit()
            return jsonify({'success': True})
        except sqlite3.IntegrityError:
            return jsonify({'error': 'Email already exists'}), 400
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        start = time.time()
        data = request.json
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        remember_me = data.get('rememberMe', False)
        
        user = get_user_fast(email)
        if not user or user['password_hash'] != hash_password(password):
            return jsonify({'error': 'Invalid credentials'}), 401
        
        session['user_email'] = user['email']
        if remember_me:
            session.permanent = True
        
        print(f"Login took {time.time() - start:.3f}s")
        return jsonify({
            'success': True,
            'user': {
                'email': user['email'],
                'fullName': user['full_name'],
                'paymentVerified': bool(user['payment_verified']),
                'isAdmin': bool(user['is_admin']),
                'displayName': user.get('display_name')
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/me', methods=['GET'])
def me():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_fast(email)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'email': user['email'],
        'fullName': user['full_name'],
        'paymentVerified': bool(user['payment_verified']),
        'isAdmin': bool(user['is_admin']),
        'displayName': user.get('display_name')
    })

@app.route('/api/set-display-name', methods=['POST'])
def set_display_name():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    display_name = data.get('displayName', '').strip()
    
    if not display_name or len(display_name) < 2:
        return jsonify({'error': 'Name must be at least 2 characters'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET display_name = ? WHERE email = ?", (display_name, email))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'displayName': display_name})

@app.route('/api/check-access', methods=['GET'])
def check_access():
    """Lightning fast access check - single query, no extra data"""
    email = session.get('user_email')
    if not email:
        return jsonify({'hasAccess': False}), 401
    
    conn = sqlite3.connect(DB_PATH)
    result = conn.execute(
        "SELECT payment_verified, is_admin FROM users WHERE email = ?", 
        (email.lower(),)
    ).fetchone()
    conn.close()
    
    if not result:
        return jsonify({'hasAccess': False}), 401
    
    has_access = bool(result[0]) or bool(result[1])
    return jsonify({'hasAccess': has_access})

@app.route('/api/submit-payment', methods=['POST'])
def submit_payment():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    bank_name = data.get('bankName', '').strip()
    reference = data.get('reference', '').strip()
    
    if not bank_name or not reference:
        return jsonify({'error': 'Missing fields'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO payments (user_email, bank_name, reference) VALUES (?, ?, ?)",
                (email, bank_name, reference))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Cached settings - super fast"""
    settings = get_cached_settings()
    return jsonify({'settings': settings})

@app.route('/api/admin/update-settings', methods=['POST'])
def update_settings():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    setting_key = data.get('key')
    setting_value = data.get('value')
    
    if setting_key and setting_value is not None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE settings SET setting_value = ?, updated_at = CURRENT_TIMESTAMP WHERE setting_key = ?", 
                    (str(setting_value), setting_key))
        conn.commit()
        conn.close()
        # Clear cache
        global settings_cache_time
        settings_cache_time = 0
        return jsonify({'success': True})
    return jsonify({'error': 'Invalid data'}), 400

@app.route('/api/admin/pending-payments', methods=['GET'])
def pending_payments():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    payments = [dict(p) for p in conn.execute("SELECT * FROM payments WHERE status = 'pending' ORDER BY submitted_at DESC").fetchall()]
    conn.close()
    return jsonify({'payments': payments})

@app.route('/api/admin/verify', methods=['POST'])
def verify_payment():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    payment_id = data.get('paymentId')
    
    conn = sqlite3.connect(DB_PATH)
    payment = conn.execute("SELECT user_email FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if payment:
        conn.execute("UPDATE payments SET status = 'approved' WHERE id = ?", (payment_id,))
        conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (payment[0],))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    conn.close()
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/admin/users', methods=['GET'])
def list_users():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    users = [dict(u) for u in conn.execute("SELECT email, full_name, payment_verified, display_name FROM users WHERE is_admin = 0").fetchall()]
    conn.close()
    return jsonify({'users': users})

@app.route('/api/admin/verify-user-payment', methods=['POST'])
def verify_user():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    target = data.get('email', '').lower()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (target,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# Socket events
active_users = {}

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    email = session.get('user_email')
    if email and email in active_users:
        del active_users[email]
        emit('online_count', {'count': len(active_users)}, room='main_chat', broadcast=True)

@socketio.on('join_chat')
def handle_join_chat():
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user_fast(email)
    if not user or (not user['payment_verified'] and not user['is_admin']):
        emit('error', {'message': 'Payment required'})
        return
    
    display_name = user.get('display_name', user['full_name'].split()[0])
    active_users[email] = {'sid': request.sid, 'name': display_name}
    join_room('main_chat')
    
    # Get recent messages (limit 30 for speed)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    messages = [dict(m) for m in conn.execute("SELECT id, sender_name, message_text, timestamp, is_system FROM messages ORDER BY timestamp DESC LIMIT 30").fetchall()]
    conn.close()
    emit('chat_history', {'messages': list(reversed(messages))})
    emit('online_count', {'count': len(active_users)}, room='main_chat')

@socketio.on('send_message')
def handle_send_message(data):
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user_fast(email)
    if not user or (not user['payment_verified'] and not user['is_admin']):
        return
    
    text = data.get('text', '').strip()
    if not text or len(text) > 500:
        return
    
    display_name = user.get('display_name', user['full_name'].split()[0])
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (sender_name, message_text, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)",
                  (display_name, text))
    conn.commit()
    msg_id = cursor.lastrowid
    conn.close()
    
    emit('new_message', {
        'id': msg_id,
        'sender': display_name,
        'text': text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': False
    }, room='main_chat')

@socketio.on('admin_broadcast')
def handle_admin_broadcast(data):
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        return
    
    message = data.get('message', '').strip()
    if not message:
        return
    
    display_name = user.get('display_name', 'Admin')
    broadcast_text = f'🔊 {display_name}: {message}'
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO messages (sender_name, message_text, is_system, timestamp) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                ('📢 ANNOUNCEMENT', broadcast_text, 1))
    conn.commit()
    conn.close()
    
    emit('new_message', {
        'id': 0,
        'sender': '📢 ANNOUNCEMENT',
        'text': broadcast_text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat')

print("=" * 50)
print("✅ Optimized Server Ready!")
print("👑 Admin: admin@chylnx.com / admin123")
print("=" * 50)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000, debug=True)