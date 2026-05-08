from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
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
from threading import Lock
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='')

# Security Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SAMESITE'] = 'None'  # Changed to None for Render
app.config['SESSION_COOKIE_SECURE'] = True  # Changed to True for HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

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
    async_mode='threading',
    ping_timeout=60,
    ping_interval=25,
    manage_session=False
)

# Database configuration
DB_DIR = os.environ.get('DB_DIR', '/tmp')
DB_PATH = os.path.join(DB_DIR, 'chylnx.db')
DB_LOCK = Lock()

# Cache configuration
settings_cache = {}
settings_cache_time = 0
CACHE_TTL = 300

# Validation patterns
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
USERNAME_REGEX = re.compile(r'^[a-zA-Z0-9\s\-_]{2,50}$')

# ======================
# DATABASE UTILITIES
# ======================

@contextmanager
def get_db():
    DB_LOCK.acquire()
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
        DB_LOCK.release()

# ✅ FIXED: Simple SHA-256 hashing without salt
def hash_password(password):
    """Hash password with SHA-256 only"""
    return hashlib.sha256(password.encode()).hexdigest()

def validate_email(email):
    return bool(EMAIL_REGEX.match(email))

def validate_username(name):
    return bool(USERNAME_REGEX.match(name))

def sanitize_input(text, max_length=500):
    if not text:
        return ''
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    return text[:max_length].strip()

def init_db():
    """Initialize database"""
    try:
        # Delete old database if exists (to start fresh with correct hashing)
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
            logger.info("🗑️ Old database deleted - creating fresh")
        
        with get_db() as conn:
            c = conn.cursor()
            
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                payment_verified INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                login_attempts INTEGER DEFAULT 0,
                locked_until TIMESTAMP
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                bank_name TEXT NOT NULL,
                reference TEXT NOT NULL,
                amount TEXT,
                payment_method TEXT,
                status TEXT DEFAULT 'pending',
                verified_by TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_at TIMESTAMP
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_name TEXT NOT NULL,
                message_text TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_system INTEGER DEFAULT 0,
                sender_email TEXT
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_email TEXT,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Create indexes
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(setting_key)")
            
            # Create admin with FIXED password hash
            admin_email = 'admin@chylnx.com'
            admin_password = 'admin123'
            admin_hash = hash_password(admin_password)
            
            logger.info(f"Creating admin: {admin_email}")
            logger.info(f"Admin password hash: {admin_hash}")
            
            c.execute(
                "INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?, ?, ?, ?, ?)",
                ('Administrator', admin_email, admin_hash, 1, 1)
            )
            
            # Default settings
            default_settings = [
                ('game_timer_hours', '24'),
                ('game_timer_minutes', '0'),
                ('game_timer_seconds', '0'),
                ('weekly_timer_days', '7'),
                ('weekly_timer_hours', '0'),
                ('weekly_timer_minutes', '0'),
                ('weekly_timer_seconds', '0'),
                ('info_bar_text', 'Welcome to Chylnx Hub! 🎮 Join our community chat!'),
                ('info_bar_color', '#667eea'),
                ('max_message_length', '500'),
                ('chat_history_limit', '50'),
                ('enable_typing_indicator', 'true')
            ]
            
            for key, value in default_settings:
                c.execute(
                    "INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)", 
                    (key, value)
                )
            
            conn.commit()
            logger.info("✅ Database initialized successfully")
            
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

# ======================
# DECORATORS
# ======================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        try:
            with get_db() as conn:
                user = conn.execute(
                    "SELECT is_admin FROM users WHERE email = ?", 
                    (session['user_email'].lower(),)
                ).fetchone()
                
                if not user or not user['is_admin']:
                    return jsonify({'error': 'Admin privileges required'}), 403
                    
        except Exception as e:
            logger.error(f"Admin check error: {e}")
            return jsonify({'error': 'Internal server error'}), 500
            
        return f(*args, **kwargs)
    return decorated_function

# ======================
# CACHE UTILITY
# ======================

def get_cached_settings(force_refresh=False):
    global settings_cache, settings_cache_time
    now = time.time()
    
    if force_refresh or (now - settings_cache_time) > CACHE_TTL or not settings_cache:
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT setting_key, setting_value FROM settings"
                ).fetchall()
                settings_cache = {row['setting_key']: row['setting_value'] for row in rows}
                settings_cache_time = now
        except Exception as e:
            logger.error(f"Settings cache error: {e}")
            return settings_cache if settings_cache else {}
            
    return settings_cache

# ======================
# USER UTILITIES
# ======================

def get_user_fast(email):
    try:
        with get_db() as conn:
            user = conn.execute(
                """SELECT id, full_name, email, password_hash, display_name, 
                   payment_verified, is_admin, login_attempts, locked_until 
                   FROM users WHERE email = ?""", 
                (email.lower(),)
            ).fetchone()
            return dict(user) if user else None
    except Exception as e:
        logger.error(f"Get user error: {e}")
        return None

def log_admin_action(admin_email, action, details=''):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_log (admin_email, action, details) VALUES (?, ?, ?)",
                (admin_email, action, str(details))
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Audit log error: {e}")

def save_message_to_db(sender_name, message_text, is_system=0, sender_email=None):
    try:
        with get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO messages 
                (sender_name, message_text, is_system, sender_email, timestamp) 
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (sender_name, message_text, is_system, sender_email)
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Save message error: {e}")
        return None

def get_recent_messages(limit=50):
    try:
        limit = min(int(limit), 100)
        with get_db() as conn:
            messages = conn.execute(
                """SELECT id, sender_name, message_text, timestamp, is_system, sender_email 
                   FROM messages 
                   ORDER BY timestamp DESC 
                   LIMIT ?""", 
                (limit,)
            ).fetchall()
            return [dict(m) for m in reversed(messages)]
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        return []

# ======================
# ROUTES - Static Files
# ======================

@app.route('/')
def index():
    return send_from_directory('.', 'login.html')

@app.route('/<path:filename>')
def serve_file(filename):
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    
    allowed_extensions = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.json'}
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in allowed_extensions:
        return jsonify({'error': 'File type not allowed'}), 403
        
    return send_from_directory('.', filename)

# ======================
# ROUTES - Authentication
# ======================

@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    """Register a new user"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400
        
        full_name = sanitize_input(data.get('fullName', ''), max_length=100)
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        
        logger.info(f"📝 Registration attempt: {email}")
        
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
        
        if not validate_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
            
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
            
        if len(full_name) < 2:
            return jsonify({'error': 'Name must be at least 2 characters'}), 400
        
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ?", 
                (email,)
            ).fetchone()
            
            if existing:
                return jsonify({'error': 'Email already registered'}), 409
            
            hashed = hash_password(password)
            logger.info(f"Creating user with hash: {hashed[:20]}...")
            
            conn.execute(
                "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
                (full_name, email, hashed)
            )
            conn.commit()
            
            # Verify user was created
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user:
                logger.info(f"✅ User registered: {email}")
                return jsonify({'success': True, 'message': 'Registration successful'}), 201
            else:
                logger.error(f"❌ User not found after insert: {email}")
                return jsonify({'error': 'Registration failed'}), 500
            
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return jsonify({'error': 'Registration failed. Please try again.'}), 500

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    """Login user"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400
        
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')
        remember_me = data.get('rememberMe', False)
        
        logger.info(f"🔑 Login attempt: {email}")
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        user = get_user_fast(email)
        
        if not user:
            logger.info(f"❌ User not found: {email}")
            return jsonify({'error': 'Invalid email or password'}), 401
        
        hashed_input = hash_password(password)
        
        logger.info(f"Password check - Input hash: {hashed_input[:20]}...")
        logger.info(f"Password check - DB hash: {user['password_hash'][:20]}...")
        
        if user['password_hash'] != hashed_input:
            logger.info(f"❌ Password mismatch for: {email}")
            return jsonify({'error': 'Invalid email or password'}), 401
        
        # Set session
        session.clear()
        session['user_email'] = user['email']
        session['user_id'] = user['id']
        session.permanent = remember_me
        
        logger.info(f"✅ Login successful: {email}")
        
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
        return jsonify({'error': 'Login failed. Please try again.'}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    email = session.get('user_email')
    session.clear()
    if email:
        logger.info(f"User logged out: {email}")
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
@login_required
def me():
    user = get_user_fast(session['user_email'])
    if not user:
        session.clear()
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'email': user['email'],
        'fullName': user['full_name'],
        'paymentVerified': bool(user['payment_verified']),
        'isAdmin': bool(user['is_admin']),
        'displayName': user.get('display_name')
    })

# ======================
# ROUTES - User Profile
# ======================

@app.route('/api/set-display-name', methods=['POST'])
@login_required
def set_display_name():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        display_name = sanitize_input(data.get('displayName', ''), max_length=50)
        
        if not display_name or len(display_name) < 2:
            return jsonify({'error': 'Name must be at least 2 characters'}), 400
            
        if not validate_username(display_name):
            return jsonify({'error': 'Name contains invalid characters'}), 400
        
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE email = ?",
                (display_name, session['user_email'])
            )
            conn.commit()
        
        return jsonify({'success': True, 'displayName': display_name})
        
    except Exception as e:
        logger.error(f"Set display name error: {e}")
        return jsonify({'error': 'Failed to update display name'}), 500

# ======================
# ROUTES - Payments
# ======================

@app.route('/api/check-access', methods=['GET'])
@login_required
def check_access():
    user = get_user_fast(session['user_email'])
    if not user:
        return jsonify({'hasAccess': False}), 401
    
    has_access = bool(user['payment_verified']) or bool(user['is_admin'])
    return jsonify({'hasAccess': has_access})

@app.route('/api/submit-payment', methods=['POST'])
@login_required
@limiter.limit("3 per hour")
def submit_payment():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        bank_name = sanitize_input(data.get('bankName', ''), max_length=100)
        reference = sanitize_input(data.get('reference', ''), max_length=200)
        payment_method = sanitize_input(data.get('method', ''), max_length=50)
        
        if not bank_name or not reference:
            return jsonify({'error': 'Bank name and reference are required'}), 400
        
        with get_db() as conn:
            conn.execute(
                "INSERT INTO payments (user_email, bank_name, reference, payment_method) VALUES (?, ?, ?, ?)",
                (session['user_email'], bank_name, reference, payment_method)
            )
            conn.commit()
        
        logger.info(f"Payment submitted: {session['user_email']}")
        return jsonify({'success': True, 'message': 'Payment proof submitted for review'})
        
    except Exception as e:
        logger.error(f"Submit payment error: {e}")
        return jsonify({'error': 'Failed to submit payment'}), 500

# ======================
# ROUTES - Settings
# ======================

@app.route('/api/settings', methods=['GET'])
def get_settings():
    settings = get_cached_settings()
    safe_settings = {k: v for k, v in settings.items() 
                     if not k.startswith('admin_') and not k.startswith('secret_')}
    return jsonify({'settings': safe_settings})

# ======================
# ROUTES - Admin
# ======================

@app.route('/api/admin/pending-payments', methods=['GET'])
@admin_required
def pending_payments():
    try:
        with get_db() as conn:
            payments = conn.execute(
                """SELECT p.*, u.full_name 
                FROM payments p 
                JOIN users u ON p.user_email = u.email 
                WHERE p.status = 'pending' 
                ORDER BY p.submitted_at DESC"""
            ).fetchall()
            return jsonify({'payments': [dict(p) for p in payments]})
    except Exception as e:
        logger.error(f"Pending payments error: {e}")
        return jsonify({'error': 'Failed to load payments'}), 500

@app.route('/api/admin/verify', methods=['POST'])
@admin_required
def verify_payment():
    try:
        data = request.get_json(silent=True)
        payment_id = data.get('paymentId')
        
        with get_db() as conn:
            payment = conn.execute(
                "SELECT user_email FROM payments WHERE id = ? AND status = 'pending'",
                (payment_id,)
            ).fetchone()
            
            if not payment:
                return jsonify({'error': 'Payment not found'}), 404
            
            conn.execute(
                "UPDATE payments SET status = 'approved', verified_by = ?, verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session['user_email'], payment_id)
            )
            conn.execute(
                "UPDATE users SET payment_verified = 1 WHERE email = ?",
                (payment['user_email'],)
            )
            conn.commit()
        
        logger.info(f"Payment verified: {payment_id}")
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Verify payment error: {e}")
        return jsonify({'error': 'Failed to verify payment'}), 500

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_users():
    try:
        with get_db() as conn:
            users = conn.execute(
                "SELECT email, full_name, payment_verified, display_name, created_at FROM users WHERE is_admin = 0 ORDER BY created_at DESC"
            ).fetchall()
            return jsonify({'users': [dict(u) for u in users]})
    except Exception as e:
        logger.error(f"List users error: {e}")
        return jsonify({'error': 'Failed to load users'}), 500

@app.route('/api/admin/verify-user-payment', methods=['POST'])
@admin_required
def verify_user():
    try:
        data = request.get_json(silent=True)
        target_email = data.get('email', '').lower().strip()
        
        with get_db() as conn:
            conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (target_email,))
            conn.commit()
        
        logger.info(f"User verified: {target_email}")
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Verify user error: {e}")
        return jsonify({'error': 'Failed to verify user'}), 500

@app.route('/api/admin/update-settings', methods=['POST'])
@admin_required
def update_settings():
    try:
        data = request.get_json(silent=True)
        setting_key = data.get('key')
        setting_value = str(data.get('value', ''))
        
        with get_db() as conn:
            conn.execute(
                "UPDATE settings SET setting_value = ?, updated_at = CURRENT_TIMESTAMP WHERE setting_key = ?",
                (setting_value, setting_key)
            )
            conn.commit()
        
        global settings_cache_time
        settings_cache_time = 0
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ======================
# SOCKET.IO EVENTS
# ======================

active_users = {}
ACTIVE_USERS_LOCK = Lock()

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('join_chat')
def handle_join_chat():
    email = session.get('user_email')
    
    if not email:
        return
    
    user = get_user_fast(email)
    if not user:
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment required'})
        return
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    
    with ACTIVE_USERS_LOCK:
        active_users[email] = {'sid': request.sid, 'name': display_name}
        online_count = len(active_users)
    
    join_room('main_chat')
    
    messages = get_recent_messages(50)
    emit('chat_history', {'messages': messages})
    emit('online_count', {'count': online_count}, room='main_chat')

@socketio.on('send_message')
def handle_send_message(data):
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user_fast(email)
    if not user:
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        return
    
    text = data.get('text', '').strip()
    if not text:
        return
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    save_message_to_db(display_name, text, sender_email=email)
    
    emit('new_message', {
        'sender': display_name,
        'text': text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': False
    }, room='main_chat')

@socketio.on('admin_broadcast')
def handle_admin_broadcast(data):
    email = session.get('user_email')
    user = get_user_fast(email) if email else None
    
    if not user or not user['is_admin']:
        return
    
    message = data.get('message', '').strip()
    if not message:
        return
    
    display_name = user.get('display_name') or 'Admin'
    broadcast_text = f'🔊 ANNOUNCEMENT from {display_name}: {message}'
    
    save_message_to_db('📢 ANNOUNCEMENT', broadcast_text, is_system=1)
    
    emit('new_message', {
        'sender': '📢 ANNOUNCEMENT',
        'text': broadcast_text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat')

# ======================
# ERROR HANDLERS
# ======================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# ======================
# MAIN
# ======================

if __name__ == '__main__':
    init_db()
    
    port = int(os.environ.get('PORT', 10000))
    
    logger.info("=" * 50)
    logger.info("✅ Chylnx Hub Server Ready!")
    logger.info("👑 Admin: admin@chylnx.com / admin123")
    logger.info("=" * 50)
    
    socketio.run(app, host='0.0.0.0', port=port, debug=False)