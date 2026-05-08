from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import eventlet
eventlet.monkey_patch()
import secrets
import sqlite3
import hashlib
import os

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True

CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# FORCE FRESH DATABASE - Use a timestamp to create unique filename
import time
DB_PATH = f'/tmp/chylnx_{int(time.time())}.db'
print(f"📁 NEW DATABASE: {DB_PATH}")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT,
        payment_verified INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        display_name TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        bank_name TEXT,
        reference TEXT,
        payment_method TEXT DEFAULT 'transfer',
        status TEXT DEFAULT 'pending'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_name TEXT,
        sender_email TEXT,
        message_text TEXT,
        is_system INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT
    )''')
    
    # Create admin
    admin_hash = hash_password('admin123')
    print(f"🔐 Admin hash: {admin_hash}")
    
    c.execute("INSERT OR IGNORE INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?,?,?,?,?)",
              ('Admin', 'admin@chylnx.com', admin_hash, 1, 1))
    
    # Insert default settings
    for key, val in [
        ('game_timer_hours','24'),('game_timer_minutes','0'),('game_timer_seconds','0'),
        ('weekly_timer_days','7'),('info_bar_text','Welcome!'),('info_bar_color','#667eea')
    ]:
        c.execute("INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?,?)", (key, val))
    
    conn.commit()
    conn.close()
    print("✅ Database ready!")

# ============ ROUTES ============

@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:filename>')
def serve_file(filename):
    return send_from_directory('.', filename)

@app.route('/api/debug', methods=['GET'])
def debug():
    """Show database contents"""
    conn = get_db()
    users = conn.execute("SELECT email, password_hash, is_admin, payment_verified FROM users").fetchall()
    conn.close()
    return jsonify({
        'db_path': DB_PATH,
        'users': [dict(u) for u in users]
    })

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json(silent=True)
        print(f"📥 Register data: {data}")
        
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        full_name = data.get('fullName', '').strip()
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        conn = get_db()
        
        # Check existing
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return jsonify({'error': 'Email already exists'}), 409
        
        # Create user
        hashed = hash_password(password)
        print(f"🔐 Creating user - hash: {hashed[:20]}...")
        
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
            (full_name, email, hashed)
        )
        conn.commit()
        
        # Verify
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        print(f"✅ User created: {dict(user) if user else 'FAILED'}")
        conn.close()
        
        if user:
            return jsonify({'success': True, 'message': 'Account created!'}), 201
        else:
            return jsonify({'error': 'Failed to create account'}), 500
        
    except Exception as e:
        print(f"❌ Register error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json(silent=True)
        print(f"📥 Login data: {data}")
        
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        
        if not user:
            conn.close()
            print(f"❌ User not found: {email}")
            return jsonify({'error': 'Invalid email or password'}), 401
        
        hashed_input = hash_password(password)
        print(f"🔑 Input hash: {hashed_input[:20]}...")
        print(f"🔑 DB hash:    {user['password_hash'][:20]}...")
        print(f"🔑 Match: {user['password_hash'] == hashed_input}")
        
        if user['password_hash'] != hashed_input:
            conn.close()
            print(f"❌ Password mismatch for: {email}")
            return jsonify({'error': 'Invalid email or password'}), 401
        
        conn.close()
        
        session['user_email'] = user['email']
        print(f"✅ Login successful: {email}")
        
        return jsonify({
            'success': True,
            'user': {
                'email': user['email'],
                'fullName': user['full_name'],
                'paymentVerified': bool(user['payment_verified']),
                'isAdmin': bool(user['is_admin']),
                'displayName': user['display_name']
            }
        })
        
    except Exception as e:
        print(f"❌ Login error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/me', methods=['GET'])
def me():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'email': user['email'],
        'fullName': user['full_name'],
        'paymentVerified': bool(user['payment_verified']),
        'isAdmin': bool(user['is_admin']),
        'displayName': user['display_name']
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM settings").fetchall()
    conn.close()
    return jsonify({'settings': {row['setting_key']: row['setting_value'] for row in rows}})

@app.route('/api/check-access', methods=['GET'])
def check_access():
    email = session.get('user_email')
    if not email:
        return jsonify({'hasAccess': False}), 401
    
    conn = get_db()
    user = conn.execute("SELECT payment_verified, is_admin FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'hasAccess': False}), 401
    
    has_access = bool(user['payment_verified']) or bool(user['is_admin'])
    return jsonify({'hasAccess': has_access})

# Socket.IO
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('join_chat')
def handle_join_chat():
    email = session.get('user_email')
    if not email:
        return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    
    if not user:
        return
    
    join_room('main_chat')
    
    conn = get_db()
    messages = conn.execute("SELECT * FROM messages ORDER BY rowid DESC LIMIT 50").fetchall()
    conn.close()
    emit('chat_history', {'messages': [dict(m) for m in reversed(messages)]})

@socketio.on('send_message')
def handle_send_message(data):
    email = session.get('user_email')
    if not email:
        return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    if not user:
        conn.close()
        return
    
    text = data.get('text', '').strip()
    if not text:
        conn.close()
        return
    
    display_name = user['display_name'] or user['full_name'].split()[0] if user['full_name'] else 'User'
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    conn.execute(
        "INSERT INTO messages (sender_name, sender_email, message_text) VALUES (?, ?, ?)",
        (display_name, email, text)
    )
    conn.commit()
    conn.close()
    
    emit('new_message', {
        'sender': display_name,
        'text': text,
        'isSystem': False
    }, room='main_chat')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 10000))
    print("=" * 50)
    print("🚀 Server starting...")
    print("👑 Admin: admin@chylnx.com / admin123")
    print("=" * 50)
    socketio.run(app, host='0.0.0.0', port=port, debug=True)