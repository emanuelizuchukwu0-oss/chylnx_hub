from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import secrets
import sqlite3
import hashlib
import os
from datetime import datetime, timedelta

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

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Initialize database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
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
    
    # Payments table
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        bank_name TEXT NOT NULL,
        reference TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Messages table
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_name TEXT NOT NULL,
        message_text TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_system INTEGER DEFAULT 0
    )''')
    
    # Settings table
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        setting_key TEXT UNIQUE NOT NULL,
        setting_value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Indexes for faster queries
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp DESC)")
    
    # Create admin if not exists
    c.execute("SELECT * FROM users WHERE email = 'admin@chylnx.com'")
    if not c.fetchone():
        c.execute("INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?, ?, ?, ?, ?)",
                   ('Administrator', 'admin@chylnx.com', hash_password('admin123'), 1, 1))
    
    # Initialize default settings
    settings = [
        ('game_timer_hours', '24'),
        ('game_timer_minutes', '0'),
        ('game_timer_seconds', '0'),
        ('weekly_timer_days', '7'),
        ('weekly_timer_hours', '0'),
        ('weekly_timer_minutes', '0'),
        ('weekly_timer_seconds', '0'),
        ('info_bar_text', 'Welcome to Chylnx Hub! 🎮 Join our community chat and connect with others.'),
        ('info_bar_color', '#667eea')
    ]
    
    for key, value in settings:
        c.execute("INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
    
    conn.commit()
    conn.close()
    print("✅ Database ready")

init_db()

# Helper functions
def get_user(email):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    conn.close()
    return dict(user) if user else None

def save_message_to_db(sender_name, message_text, is_system=0):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (sender_name, message_text, is_system, timestamp) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                  (sender_name, message_text, is_system))
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return message_id

def get_recent_messages(limit=50):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    messages = conn.execute("SELECT id, sender_name, message_text, timestamp, is_system FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(m) for m in reversed(messages)]

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
        data = request.json
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        remember_me = data.get('rememberMe', False)
        
        user = get_user(email)
        if not user or user['password_hash'] != hash_password(password):
            return jsonify({'error': 'Invalid credentials'}), 401
        
        session['user_email'] = user['email']
        if remember_me:
            session.permanent = True
        
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

@app.route('/api/auth/restore-session', methods=['POST'])
def restore_session():
    try:
        data = request.json
        email = data.get('email', '').lower().strip()
        
        user = get_user(email)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        session['user_email'] = user['email']
        session.permanent = True
        
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

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.pop('user_email', None)
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
def me():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    user = get_user(email)
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
    email = session.get('user_email')
    if not email:
        return jsonify({'hasAccess': False}), 401
    user = get_user(email)
    if user and user['is_admin']:
        return jsonify({'hasAccess': True})
    return jsonify({'hasAccess': bool(user['payment_verified']) if user else False})

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

@app.route('/api/messages', methods=['GET'])
def get_messages():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user(email)
    if not user or (not user['payment_verified'] and not user['is_admin']):
        return jsonify({'error': 'Access denied'}), 403
    
    messages = get_recent_messages(50)
    return jsonify({'messages': messages})

# Settings Routes
@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    settings = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
    conn.close()
    return jsonify({'settings': {s['setting_key']: s['setting_value'] for s in settings}})

@app.route('/api/admin/update-settings', methods=['POST'])
def update_settings():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    setting_key = data.get('key')
    setting_value = data.get('value')
    
    if setting_key and setting_value is not None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE settings SET setting_value = ?, updated_at = CURRENT_TIMESTAMP WHERE setting_key = ?", (str(setting_value), setting_key))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    return jsonify({'error': 'Invalid data'}), 400

# Admin Routes
@app.route('/api/admin/pending-payments', methods=['GET'])
def pending_payments():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user(email)
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
    
    user = get_user(email)
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
    
    user = get_user(email)
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
    
    user = get_user(email)
    if not user or not user['is_admin']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    target = data.get('email', '').lower()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (target,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== SOCKET.IO EVENTS (FIXED) ====================

# Store active users
active_users = {}

@socketio.on('connect')
def handle_connect():
    print(f'✅ Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'❌ Client disconnected: {request.sid}')
    # Remove user from active users
    email = session.get('user_email')
    if email and email in active_users:
        del active_users[email]
        # Broadcast updated count
        emit('online_count', {'count': len(active_users)}, room='main_chat', broadcast=True)

@socketio.on('join_chat')
def handle_join_chat():
    email = session.get('user_email')
    print(f'Join chat attempt from: {email}')
    
    if not email:
        emit('error', {'message': 'Not authenticated'})
        return
    
    user = get_user(email)
    if not user:
        emit('error', {'message': 'User not found'})
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment required'})
        return
    
    # Store user
    display_name = user.get('display_name', user['full_name'].split()[0])
    active_users[email] = {
        'sid': request.sid,
        'name': display_name,
        'is_admin': user['is_admin']
    }
    
    # Join the room
    join_room('main_chat')
    print(f'✅ User {display_name} joined main_chat')
    
    # Send recent messages to this user
    messages = get_recent_messages(50)
    emit('chat_history', {'messages': messages})
    
    # Update online count for everyone
    emit('online_count', {'count': len(active_users)}, room='main_chat')
    
    # Send join notification to everyone else
    join_message = f'{display_name} joined the chat'
    save_message_to_db('System', join_message, is_system=1)
    emit('new_message', {
        'id': 0,
        'sender': 'System',
        'text': join_message,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat', skip_sid=request.sid)

@socketio.on('send_message')
def handle_send_message(data):
    email = session.get('user_email')
    print(f'Send message from: {email}, data: {data}')
    
    if not email:
        emit('error', {'message': 'Not authenticated'})
        return
    
    user = get_user(email)
    if not user:
        emit('error', {'message': 'User not found'})
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment required'})
        return
    
    message_text = data.get('text', '').strip()
    if not message_text:
        return
    
    if len(message_text) > 500:
        emit('error', {'message': 'Message too long'})
        return
    
    display_name = user.get('display_name', user['full_name'].split()[0])
    
    # Save to database
    message_id = save_message_to_db(display_name, message_text, is_system=0)
    
    # Broadcast to everyone in the room
    emit('new_message', {
        'id': message_id,
        'sender': display_name,
        'text': message_text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': False
    }, room='main_chat')
    
    print(f'✅ Message broadcasted: {display_name}: {message_text}')

@socketio.on('admin_broadcast')
def handle_admin_broadcast(data):
    email = session.get('user_email')
    print(f'Admin broadcast from: {email}')
    
    if not email:
        emit('error', {'message': 'Not authenticated'})
        return
    
    user = get_user(email)
    if not user or not user['is_admin']:
        emit('error', {'message': 'Unauthorized'})
        return
    
    message = data.get('message', '').strip()
    if not message:
        return
    
    display_name = user.get('display_name', 'Admin')
    broadcast_text = f'🔊 ANNOUNCEMENT from {display_name}: {message}'
    
    # Save to database
    save_message_to_db('📢 ANNOUNCEMENT', broadcast_text, is_system=1)
    
    # Broadcast to everyone
    emit('new_message', {
        'id': 0,
        'sender': '📢 ANNOUNCEMENT',
        'text': broadcast_text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat')
    
    print(f'✅ Admin broadcast sent: {broadcast_text}')

@socketio.on('typing')
def handle_typing(data):
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user(email)
    if not user:
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        return
    
    display_name = user.get('display_name', user['full_name'].split()[0])
    is_typing = data.get('isTyping', False)
    
    emit('user_typing', {
        'user': display_name,
        'isTyping': is_typing
    }, room='main_chat', include_self=False)

print("=" * 50)
print("✅ Server is ready!")
print("👑 Admin: admin@chylnx.com / admin123")
print("=" * 50)

# For Gunicorn
app = app

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000, debug=True)