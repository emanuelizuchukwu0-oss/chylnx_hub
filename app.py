from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import eventlet
eventlet.monkey_patch()
import secrets
import sqlite3
import hashlib
import os
import re
import time
import logging
from datetime import datetime, timedelta
from functools import wraps

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# CORS
allowed_origins = os.environ.get('ALLOWED_ORIGINS', '*')
CORS(app, supports_credentials=True, origins=allowed_origins)

# Socket.IO
socketio = SocketIO(app, cors_allowed_origins=allowed_origins, async_mode='threading')

# Database path
DB_DIR = os.environ.get('DB_DIR', '/tmp')
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, 'chylnx.db')

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
        payment_method TEXT DEFAULT 'transfer',
        status TEXT DEFAULT 'pending',
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_name TEXT NOT NULL,
        sender_email TEXT,
        message_text TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_system INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT
    )''')
    
    # Create admin
    c.execute("SELECT * FROM users WHERE email = 'admin@chylnx.com'")
    if not c.fetchone():
        c.execute(
            "INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?, ?, ?, ?, ?)",
            ('Administrator', 'admin@chylnx.com', hash_password('admin123'), 1, 1)
        )
    
    # Default settings
    defaults = [
        ('game_timer_hours', '24'),
        ('game_timer_minutes', '0'),
        ('game_timer_seconds', '0'),
        ('weekly_timer_days', '7'),
        ('weekly_timer_hours', '0'),
        ('weekly_timer_minutes', '0'),
        ('weekly_timer_seconds', '0'),
        ('info_bar_text', 'Welcome to Chylnx Hub! 🎮'),
        ('info_bar_color', '#667eea'),
    ]
    for key, value in defaults:
        c.execute("INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
    
    conn.commit()
    conn.close()
    logger.info("✅ Database ready")

# Decorators
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Please login first'}), 401
        return f(*args, **kwargs)
    return wrapper

# Routes
@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:filename>')
def serve_file(filename):
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    return send_from_directory('.', filename)

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        full_name = data.get('fullName', '').strip()
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        print(f"Registration attempt: {email}")  # Debug log
        
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        conn = get_db()
        
        # Check existing
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return jsonify({'error': 'Email already registered'}), 409
        
        # Create user
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
            (full_name, email, hash_password(password))
        )
        conn.commit()
        conn.close()
        
        print(f"✅ User created: {email}")  # Debug log
        return jsonify({'success': True, 'message': 'Registration successful'}), 201
        
    except Exception as e:
        print(f"❌ Registration error: {str(e)}")  # Debug log
        return jsonify({'error': 'Registration failed'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        
        if not user or user['password_hash'] != hash_password(password):
            return jsonify({'error': 'Invalid email or password'}), 401
        
        session['user_email'] = user['email']
        
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
        return jsonify({'error': 'Login failed'}), 500

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
    settings = {row['setting_key']: row['setting_value'] for row in rows}
    return jsonify({'settings': settings})

@app.route('/api/check-access', methods=['GET'])
@login_required
def check_access():
    conn = get_db()
    user = conn.execute("SELECT payment_verified, is_admin FROM users WHERE email = ?", 
                        (session['user_email'],)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'hasAccess': False}), 401
    
    has_access = bool(user['payment_verified']) or bool(user['is_admin'])
    return jsonify({'hasAccess': has_access})

@app.route('/api/submit-payment', methods=['POST'])
@login_required
def submit_payment():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data'}), 400
    
    bank_name = data.get('bankName', '').strip()
    reference = data.get('reference', '').strip()
    method = data.get('method', 'transfer')
    
    if not bank_name or not reference:
        return jsonify({'error': 'Bank name and reference required'}), 400
    
    conn = get_db()
    conn.execute(
        "INSERT INTO payments (user_email, bank_name, reference, payment_method) VALUES (?, ?, ?, ?)",
        (session['user_email'], bank_name, reference, method)
    )
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Payment submitted'})

@app.route('/api/admin/pending-payments', methods=['GET'])
@login_required
def pending_payments():
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE email = ?", (session['user_email'],)).fetchone()
    if not user or not user['is_admin']:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
    
    payments = conn.execute(
        "SELECT p.*, u.full_name FROM payments p JOIN users u ON p.user_email = u.email WHERE p.status = 'pending' ORDER BY p.submitted_at DESC"
    ).fetchall()
    conn.close()
    return jsonify({'payments': [dict(p) for p in payments]})

@app.route('/api/admin/verify', methods=['POST'])
@login_required
def verify_payment():
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE email = ?", (session['user_email'],)).fetchone()
    if not user or not user['is_admin']:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json(silent=True)
    payment_id = data.get('paymentId')
    
    payment = conn.execute("SELECT user_email FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if payment:
        conn.execute("UPDATE payments SET status = 'approved' WHERE id = ?", (payment_id,))
        conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (payment['user_email'],))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    
    conn.close()
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/admin/users', methods=['GET'])
@login_required
def list_users():
    conn = get_db()
    user = conn.execute("SELECT is_admin FROM users WHERE email = ?", (session['user_email'],)).fetchone()
    if not user or not user['is_admin']:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
    
    users = conn.execute("SELECT email, full_name, payment_verified, display_name, created_at FROM users WHERE is_admin = 0").fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in users]})

# Socket events
active_users = {}

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('join_chat')
def handle_join_chat():
    email = session.get('user_email')
    if not email:
        emit('error', {'message': 'Please login first'})
        return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    
    if not user:
        emit('error', {'message': 'User not found'})
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment required'})
        return
    
    display_name = user['display_name'] or user['full_name'].split()[0]
    join_room('main_chat')
    
    # Send history
    conn = get_db()
    messages = conn.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT 50").fetchall()
    conn.close()
    emit('chat_history', {'messages': [dict(m) for m in reversed(messages)]})

@socketio.on('send_message')
def handle_send_message(data):
    email = session.get('user_email')
    if not email:
        return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    if not user or (not user['payment_verified'] and not user['is_admin']):
        conn.close()
        return
    
    text = data.get('text', '').strip()
    if not text:
        conn.close()
        return
    
    display_name = user['display_name'] or user['full_name'].split()[0]
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    conn.execute(
        "INSERT INTO messages (sender_name, sender_email, message_text) VALUES (?, ?, ?)",
        (display_name, email, text)
    )
    conn.commit()
    message_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    
    emit('new_message', {
        'id': message_id,
        'sender': display_name,
        'text': text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': False
    }, room='main_chat')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 10000))
    print("=" * 50)
    print("✅ Server starting...")
    print("👑 Admin: admin@chylnx.com / admin123")
    print("=" * 50)
    socketio.run(app, host='0.0.0.0', port=port, debug=False)