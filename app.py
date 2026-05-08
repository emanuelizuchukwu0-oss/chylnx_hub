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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='')

# Security Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SECURE_COOKIES', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# CORS Configuration
allowed_origins = os.environ.get('ALLOWED_ORIGINS', '*')
CORS(app, 
     supports_credentials=True,
     origins=allowed_origins,
     allow_headers=['Content-Type', 'Authorization'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

# Socket.IO Configuration
socketio = SocketIO(
    app, 
    cors_allowed_origins=allowed_origins,
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    manage_session=False
)

# Database configuration
DB_DIR = os.environ.get('DB_DIR', '/tmp')
DB_PATH = os.path.join(DB_DIR, 'chylnx.db')

# Validation patterns
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# ======================
# DATABASE UTILITIES
# ======================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    """Hash password with SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def validate_email(email):
    return bool(EMAIL_REGEX.match(email))

def sanitize_input(text, max_length=500):
    if not text:
        return ''
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    return text[:max_length].strip()

def init_db():
    """Initialize database"""
    try:
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
        admin_email = 'admin@chylnx.com'
        admin_hash = hash_password('admin123')
        
        c.execute("DELETE FROM users WHERE email = ?", (admin_email,))
        c.execute(
            "INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?, ?, ?, ?, ?)",
            ('Admin', admin_email, admin_hash, 1, 1)
        )
        logger.info(f"Admin created: {admin_email}")
        logger.info(f"Admin hash: {admin_hash}")
        
        # Default settings
        defaults = [
            ('game_timer_hours', '24'), ('game_timer_minutes', '0'), ('game_timer_seconds', '0'),
            ('weekly_timer_days', '7'), ('info_bar_text', 'Welcome to Chylnx Hub! 🎮'),
            ('info_bar_color', '#667eea')
        ]
        for key, value in defaults:
            c.execute("INSERT OR REPLACE INTO settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")
        
    except Exception as e:
        logger.error(f"Database init error: {e}")
        raise

# ======================
# DECORATORS
# ======================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Please login first'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Please login first'}), 401
        
        conn = get_db()
        user = conn.execute("SELECT is_admin FROM users WHERE email = ?", 
                           (session['user_email'],)).fetchone()
        conn.close()
        
        if not user or not user['is_admin']:
            return jsonify({'error': 'Admin only'}), 403
        return f(*args, **kwargs)
    return decorated

# ======================
# ROUTES
# ======================

@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:filename>')
def serve_file(filename):
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    return send_from_directory('.', filename)

@app.route('/api/test-register', methods=['GET'])
def test_register():
    """Test database directly"""
    try:
        conn = get_db()
        
        # Check tables
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        
        # Create test user
        test_email = 'testuser@test.com'
        test_hash = hash_password('test123')
        
        conn.execute("DELETE FROM users WHERE email = ?", (test_email,))
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
            ('Test User', test_email, test_hash)
        )
        conn.commit()
        
        user = conn.execute("SELECT * FROM users WHERE email = ?", (test_email,)).fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'tables': [t['name'] for t in tables],
            'test_user': dict(user) if user else None,
            'test_hash': test_hash
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json(silent=True)
        logger.info(f"Register attempt with data: {data}")
        
        if not data:
            return jsonify({'error': 'No data received'}), 400
        
        full_name = sanitize_input(data.get('fullName', ''), max_length=100)
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
        
        if not validate_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        if len(full_name) < 2:
            return jsonify({'error': 'Name must be at least 2 characters'}), 400
        
        conn = get_db()
        
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return jsonify({'error': 'Email already registered'}), 409
        
        hashed = hash_password(password)
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
            (full_name, email, hashed)
        )
        conn.commit()
        conn.close()
        
        logger.info(f"✅ User registered: {email}")
        return jsonify({'success': True, 'message': 'Account created!'}), 201
        
    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({'error': 'Registration failed'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json(silent=True)
        logger.info(f"Login attempt: {data.get('email') if data else 'no data'}")
        
        if not data:
            return jsonify({'error': 'No data received'}), 400
        
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        
        if not user:
            conn.close()
            logger.info(f"User not found: {email}")
            return jsonify({'error': 'Invalid email or password'}), 401
        
        input_hash = hash_password(password)
        logger.info(f"Input hash: {input_hash[:20]}...")
        logger.info(f"DB hash:    {user['password_hash'][:20]}...")
        logger.info(f"Match: {input_hash == user['password_hash']}")
        
        if input_hash != user['password_hash']:
            conn.close()
            return jsonify({'error': 'Invalid email or password'}), 401
        
        conn.close()
        
        session['user_email'] = user['email']
        logger.info(f"✅ Login: {email}")
        
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
        logger.error(f"Login error: {e}")
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
        'displayName': user.get('display_name')
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

@app.route('/api/submit-payment', methods=['POST'])
@login_required
def submit_payment():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        bank_name = sanitize_input(data.get('bankName', ''), max_length=100)
        reference = sanitize_input(data.get('reference', ''), max_length=200)
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
    except Exception as e:
        return jsonify({'error': 'Failed to submit payment'}), 500

@app.route('/api/admin/pending-payments', methods=['GET'])
@admin_required
def pending_payments():
    conn = get_db()
    payments = conn.execute(
        "SELECT p.*, u.full_name FROM payments p JOIN users u ON p.user_email = u.email WHERE p.status = 'pending' ORDER BY p.submitted_at DESC"
    ).fetchall()
    conn.close()
    return jsonify({'payments': [dict(p) for p in payments]})

@app.route('/api/admin/verify', methods=['POST'])
@admin_required
def verify_payment():
    data = request.get_json(silent=True)
    payment_id = data.get('paymentId')
    
    conn = get_db()
    payment = conn.execute("SELECT user_email FROM payments WHERE id = ? AND status = 'pending'", (payment_id,)).fetchone()
    
    if payment:
        conn.execute("UPDATE payments SET status = 'approved' WHERE id = ?", (payment_id,))
        conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (payment['user_email'],))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    
    conn.close()
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_users():
    conn = get_db()
    users = conn.execute("SELECT email, full_name, payment_verified, display_name, created_at FROM users WHERE is_admin = 0 ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in users]})

# ======================
# SOCKET.IO EVENTS
# ======================

@socketio.on('connect')
def handle_connect():
    logger.info(f"Connected: {request.sid}")

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
    
    display_name = user['display_name'] or (user['full_name'].split()[0] if user['full_name'] else 'User')
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    conn.execute("INSERT INTO messages (sender_name, sender_email, message_text) VALUES (?, ?, ?)",
                 (display_name, email, text))
    conn.commit()
    conn.close()
    
    emit('new_message', {'sender': display_name, 'text': text, 'isSystem': False}, room='main_chat')

# ======================
# MAIN
# ======================

# ✅ Initialize database BEFORE anything else
print("=" * 50)
print("🚀 Starting Chylnx Hub...")
init_db()
print("=" * 50)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)