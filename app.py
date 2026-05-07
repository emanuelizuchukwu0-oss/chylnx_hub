from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
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
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SECURE_COOKIES', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_DOMAIN'] = None  # Set to your domain in production
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)  # Reduced from 30 days
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# CORS Configuration - Restrict in production
allowed_origins = os.environ.get('ALLOWED_ORIGINS', '*')
if allowed_origins == '*':
    logger.warning("CORS set to allow all origins. Restrict this in production!")
    
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
    max_http_buffer_size=1e8,
    manage_session=False  # Let Flask handle sessions
)

# Database configuration
DB_DIR = os.environ.get('DB_DIR', '/tmp')
DB_PATH = os.path.join(DB_DIR, 'chylnx.db')
DB_LOCK = Lock()  # Thread-safe database access

# Cache configuration
settings_cache = {}
settings_cache_time = 0
CACHE_TTL = 300  # 5 minutes

# Validation patterns
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
USERNAME_REGEX = re.compile(r'^[a-zA-Z0-9\s\-_]{2,50}$')

# ======================
# DATABASE UTILITIES
# ======================

@contextmanager
def get_db():
    """Thread-safe database connection context manager"""
    DB_LOCK.acquire()
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
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

def hash_password(password):
    """Hash password with SHA-256 + salt"""
    salt = os.environ.get('PASSWORD_SALT', 'chylnx_salt_2024')
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()

def validate_email(email):
    """Validate email format"""
    return bool(EMAIL_REGEX.match(email))

def validate_username(name):
    """Validate username/nickname format"""
    return bool(USERNAME_REGEX.match(name))

def sanitize_input(text, max_length=500):
    """Basic input sanitization"""
    if not text:
        return ''
    # Remove control characters
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    # Truncate to max length
    return text[:max_length].strip()

def init_db():
    """Initialize database with proper error handling"""
    try:
        with get_db() as conn:
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                login_attempts INTEGER DEFAULT 0,
                locked_until TIMESTAMP
            )''')
            
            # Payments table
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
            
            # Messages table
            c.execute('''CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_name TEXT NOT NULL,
                message_text TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_system INTEGER DEFAULT 0,
                sender_email TEXT
            )''')
            
            # Settings table
            c.execute('''CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT
            )''')
            
            # Audit log table
            c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_email TEXT,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Create indexes for performance
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_admin ON users(is_admin)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_email)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(setting_key)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp DESC)")
            
            # Create default admin if not exists
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@chylnx.com')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
            
            c.execute("SELECT * FROM users WHERE email = ?", (admin_email.lower(),))
            if not c.fetchone():
                c.execute(
                    """INSERT INTO users 
                    (full_name, email, password_hash, is_admin, payment_verified) 
                    VALUES (?, ?, ?, ?, ?)""",
                    ('Administrator', admin_email.lower(), hash_password(admin_password), 1, 1)
                )
                logger.info(f"Default admin created: {admin_email}")
            
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
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin privileges"""
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
    """Get settings with caching"""
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
                logger.debug("Settings cache refreshed")
        except Exception as e:
            logger.error(f"Settings cache error: {e}")
            # Return existing cache if available, else empty dict
            return settings_cache if settings_cache else {}
            
    return settings_cache

# ======================
# USER UTILITIES
# ======================

def get_user_fast(email):
    """Optimized user fetch with connection pooling"""
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
    """Log admin actions for audit trail"""
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
    """Save message to database"""
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
    """Get recent messages with limit"""
    try:
        limit = min(int(limit), 100)  # Cap at 100
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
    # Security: Prevent directory traversal
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    
    # Only serve specific file types
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
        
        # Validation
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
        
        if not validate_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
            
        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
            
        if len(full_name) < 2:
            return jsonify({'error': 'Name must be at least 2 characters'}), 400
        
        with get_db() as conn:
            # Check if email already exists
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ?", 
                (email,)
            ).fetchone()
            
            if existing:
                return jsonify({'error': 'Email already registered'}), 409
            
            # Create user
            conn.execute(
                "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
                (full_name, email, hash_password(password))
            )
            conn.commit()
            
            logger.info(f"New user registered: {email}")
            return jsonify({'success': True, 'message': 'Registration successful'}), 201
            
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
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        user = get_user_fast(email)
        
        # Check if account is locked
        if user and user.get('locked_until'):
            locked_until = datetime.fromisoformat(user['locked_until'])
            if locked_until > datetime.now():
                minutes_left = (locked_until - datetime.now()).seconds // 60
                return jsonify({
                    'error': f'Account temporarily locked. Try again in {minutes_left} minutes.'
                }), 429
        
        # Verify credentials
        if not user or user['password_hash'] != hash_password(password):
            # Increment failed attempts
            if user:
                with get_db() as conn:
                    attempts = (user.get('login_attempts', 0) + 1)
                    if attempts >= 5:
                        locked_until = datetime.now() + timedelta(minutes=15)
                        conn.execute(
                            "UPDATE users SET login_attempts = ?, locked_until = ? WHERE email = ?",
                            (attempts, locked_until.isoformat(), email)
                        )
                    else:
                        conn.execute(
                            "UPDATE users SET login_attempts = ? WHERE email = ?",
                            (attempts, email)
                        )
                    conn.commit()
            
            return jsonify({'error': 'Invalid email or password'}), 401
        
        # Reset login attempts on success
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET login_attempts = 0, locked_until = NULL, last_login = CURRENT_TIMESTAMP WHERE email = ?",
                (email,)
            )
            conn.commit()
        
        # Set session
        session.clear()  # Prevent session fixation
        session['user_email'] = user['email']
        session['user_id'] = user['id']
        session.permanent = remember_me
        
        logger.info(f"User logged in: {email}")
        
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
    """Logout user"""
    email = session.get('user_email')
    session.clear()
    if email:
        logger.info(f"User logged out: {email}")
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
@login_required
def me():
    """Get current user info"""
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
    """Set user display name"""
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
    """Check if user has chat access"""
    user = get_user_fast(session['user_email'])
    if not user:
        return jsonify({'hasAccess': False}), 401
    
    has_access = bool(user['payment_verified']) or bool(user['is_admin'])
    return jsonify({'hasAccess': has_access})

@app.route('/api/submit-payment', methods=['POST'])
@login_required
@limiter.limit("3 per hour")
def submit_payment():
    """Submit payment proof"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        bank_name = sanitize_input(data.get('bankName', ''), max_length=100)
        reference = sanitize_input(data.get('reference', ''), max_length=200)
        amount = sanitize_input(str(data.get('amount', '')), max_length=20)
        payment_method = sanitize_input(data.get('method', ''), max_length=50)
        
        if not bank_name or not reference:
            return jsonify({'error': 'Bank name and reference are required'}), 400
        
        # Check for duplicate reference
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM payments WHERE reference = ?",
                (reference,)
            ).fetchone()
            
            if existing:
                return jsonify({'error': 'This reference has already been submitted'}), 409
            
            conn.execute(
                """INSERT INTO payments 
                (user_email, bank_name, reference, amount, payment_method) 
                VALUES (?, ?, ?, ?, ?)""",
                (session['user_email'], bank_name, reference, amount, payment_method)
            )
            conn.commit()
        
        logger.info(f"Payment submitted by {session['user_email']}: {reference}")
        return jsonify({'success': True, 'message': 'Payment proof submitted for review'})
        
    except Exception as e:
        logger.error(f"Submit payment error: {e}")
        return jsonify({'error': 'Failed to submit payment'}), 500

# ======================
# ROUTES - Settings
# ======================

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get public settings"""
    settings = get_cached_settings()
    
    # Remove sensitive settings
    safe_settings = {k: v for k, v in settings.items() 
                     if not k.startswith('admin_') and not k.startswith('secret_')}
    
    return jsonify({'settings': safe_settings})

# ======================
# ROUTES - Admin
# ======================

@app.route('/api/admin/update-settings', methods=['POST'])
@admin_required
def update_settings():
    """Update site settings (admin only)"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        setting_key = sanitize_input(data.get('key', ''), max_length=50)
        setting_value = str(data.get('value', ''))
        
        if not setting_key:
            return jsonify({'error': 'Setting key required'}), 400
        
        # Whitelist of allowed settings
        allowed_settings = {
            'game_timer_hours', 'game_timer_minutes', 'game_timer_seconds',
            'weekly_timer_days', 'weekly_timer_hours', 'weekly_timer_minutes',
            'weekly_timer_seconds', 'info_bar_text', 'info_bar_color',
            'max_message_length', 'chat_history_limit', 'enable_typing_indicator'
        }
        
        if setting_key not in allowed_settings:
            return jsonify({'error': 'Invalid setting key'}), 400
        
        # Validate specific settings
        if 'timer' in setting_key:
            try:
                val = int(setting_value)
                if val < 0 or ('hours' in setting_key and val > 168):
                    return jsonify({'error': 'Invalid timer value'}), 400
            except ValueError:
                return jsonify({'error': 'Timer must be a number'}), 400
        
        if setting_key == 'info_bar_text' and len(setting_value) > 200:
            return jsonify({'error': 'Info bar text too long'}), 400
        
        with get_db() as conn:
            conn.execute(
                """UPDATE settings 
                SET setting_value = ?, updated_at = CURRENT_TIMESTAMP, updated_by = ? 
                WHERE setting_key = ?""",
                (setting_value, session['user_email'], setting_key)
            )
            conn.commit()
        
        # Invalidate cache
        global settings_cache_time
        settings_cache_time = 0
        
        log_admin_action(session['user_email'], 'update_setting', f"{setting_key}={setting_value}")
        logger.info(f"Setting updated by {session['user_email']}: {setting_key}")
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        return jsonify({'error': 'Failed to update settings'}), 500

@app.route('/api/admin/pending-payments', methods=['GET'])
@admin_required
def pending_payments():
    """Get pending payments (admin only)"""
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
    """Verify a payment (admin only)"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        payment_id = data.get('paymentId')
        if not payment_id:
            return jsonify({'error': 'Payment ID required'}), 400
        
        with get_db() as conn:
            # Get payment details
            payment = conn.execute(
                "SELECT * FROM payments WHERE id = ? AND status = 'pending'",
                (payment_id,)
            ).fetchone()
            
            if not payment:
                return jsonify({'error': 'Payment not found or already processed'}), 404
            
            # Update payment status
            conn.execute(
                """UPDATE payments 
                SET status = 'approved', verified_by = ?, verified_at = CURRENT_TIMESTAMP 
                WHERE id = ?""",
                (session['user_email'], payment_id)
            )
            
            # Verify user
            conn.execute(
                "UPDATE users SET payment_verified = 1 WHERE email = ?",
                (payment['user_email'],)
            )
            
            conn.commit()
        
        log_admin_action(
            session['user_email'], 
            'verify_payment', 
            f"Payment {payment_id} for {payment['user_email']}"
        )
        
        logger.info(f"Payment verified by {session['user_email']}: {payment_id}")
        return jsonify({'success': True, 'message': 'Payment verified successfully'})
        
    except Exception as e:
        logger.error(f"Verify payment error: {e}")
        return jsonify({'error': 'Failed to verify payment'}), 500

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_users():
    """List all users (admin only)"""
    try:
        with get_db() as conn:
            users = conn.execute(
                """SELECT email, full_name, payment_verified, display_name, 
                   created_at, last_login 
                FROM users 
                WHERE is_admin = 0 
                ORDER BY created_at DESC"""
            ).fetchall()
            
            return jsonify({'users': [dict(u) for u in users]})
            
    except Exception as e:
        logger.error(f"List users error: {e}")
        return jsonify({'error': 'Failed to load users'}), 500

@app.route('/api/admin/verify-user-payment', methods=['POST'])
@admin_required
def verify_user():
    """Manually verify a user (admin only)"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        target_email = data.get('email', '').lower().strip()
        
        if not validate_email(target_email):
            return jsonify({'error': 'Invalid email'}), 400
        
        with get_db() as conn:
            result = conn.execute(
                "UPDATE users SET payment_verified = 1 WHERE email = ?",
                (target_email,)
            )
            conn.commit()
            
            if result.rowcount == 0:
                return jsonify({'error': 'User not found'}), 404
        
        log_admin_action(session['user_email'], 'manual_verify', target_email)
        logger.info(f"User manually verified by {session['user_email']}: {target_email}")
        
        return jsonify({'success': True, 'message': f'{target_email} verified'})
        
    except Exception as e:
        logger.error(f"Verify user error: {e}")
        return jsonify({'error': 'Failed to verify user'}), 500

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    """Get admin dashboard stats"""
    try:
        with get_db() as conn:
            total_users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()['count']
            verified_users = conn.execute(
                "SELECT COUNT(*) as count FROM users WHERE payment_verified = 1 OR is_admin = 1"
            ).fetchone()['count']
            pending_payments = conn.execute(
                "SELECT COUNT(*) as count FROM payments WHERE status = 'pending'"
            ).fetchone()['count']
            total_messages = conn.execute(
                "SELECT COUNT(*) as count FROM messages"
            ).fetchone()['count']
            
            return jsonify({
                'totalUsers': total_users,
                'verifiedUsers': verified_users,
                'pendingPayments': pending_payments,
                'totalMessages': total_messages
            })
            
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'error': 'Failed to load stats'}), 500

# ======================
# SOCKET.IO EVENTS
# ======================

active_users = {}
ACTIVE_USERS_LOCK = Lock()

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    email = session.get('user_email')
    
    with ACTIVE_USERS_LOCK:
        if email and email in active_users:
            del active_users[email]
            count = len(active_users)
        else:
            count = len(active_users)
    
    logger.info(f"Client disconnected: {request.sid}, email: {email}")
    socketio.emit('online_count', {'count': count}, room='main_chat')

@socketio.on('join_chat')
def handle_join_chat():
    """Handle user joining chat"""
    email = session.get('user_email')
    logger.info(f'Join chat request from: {email}')
    
    if not email:
        emit('error', {'message': 'Authentication required'})
        return
    
    user = get_user_fast(email)
    if not user:
        emit('error', {'message': 'User not found'})
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment verification required to access chat'})
        return
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    
    with ACTIVE_USERS_LOCK:
        active_users[email] = {
            'sid': request.sid,
            'name': display_name,
            'is_admin': bool(user['is_admin'])
        }
        online_count = len(active_users)
    
    join_room('main_chat')
    
    logger.info(f'✅ {display_name} joined chat (admin={user["is_admin"]})')
    
    # Send chat history
    settings = get_cached_settings()
    history_limit = int(settings.get('chat_history_limit', 50))
    messages = get_recent_messages(history_limit)
    emit('chat_history', {'messages': messages})
    
    # Update online count
    emit('online_count', {'count': online_count}, room='main_chat')
    
    # Send system notification (only to others)
    if not user['is_admin']:
        join_msg = f'{display_name} joined the chat'
        save_message_to_db('System', join_msg, is_system=1)
        emit('new_message', {
            'id': 0,
            'sender': 'System',
            'text': join_msg,
            'timestamp': datetime.now().isoformat(),
            'isSystem': True
        }, room='main_chat', skip_sid=request.sid)

@socketio.on('send_message')
def handle_send_message(data):
    """Handle incoming chat messages"""
    email = session.get('user_email')
    
    if not email:
        emit('error', {'message': 'Authentication required'})
        return
    
    user = get_user_fast(email)
    if not user:
        emit('error', {'message': 'User not found'})
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment verification required'})
        return
    
    if not isinstance(data, dict):
        return
    
    message_text = sanitize_input(
        data.get('text', ''),
        max_length=int(get_cached_settings().get('max_message_length', 500))
    )
    
    if not message_text:
        return
    
    # Prevent spam (basic rate limiting per user)
    with ACTIVE_USERS_LOCK:
        if email in active_users:
            last_msg_time = active_users[email].get('last_message_time', 0)
            if time.time() - last_msg_time < 0.5:  # 500ms cooldown
                emit('error', {'message': 'Please wait before sending another message'})
                return
            active_users[email]['last_message_time'] = time.time()
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    
    # Add admin badge
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    # Save to database
    message_id = save_message_to_db(display_name, message_text, is_system=0, sender_email=email)
    
    if message_id:
        # Broadcast message
        message_data = {
            'id': message_id,
            'sender': display_name,
            'text': message_text,
            'timestamp': datetime.now().isoformat(),
            'isSystem': False
        }
        emit('new_message', message_data, room='main_chat')

@socketio.on('admin_broadcast')
def handle_admin_broadcast(data):
    """Handle admin broadcast messages"""
    email = session.get('user_email')
    
    if not email:
        emit('error', {'message': 'Authentication required'})
        return
    
    user = get_user_fast(email)
    if not user or not user['is_admin']:
        emit('error', {'message': 'Admin privileges required'})
        return
    
    if not isinstance(data, dict):
        return
    
    message = sanitize_input(data.get('message', ''), max_length=300)
    
    if not message:
        return
    
    display_name = user.get('display_name') or 'Admin'
    broadcast_text = f'🔊 ANNOUNCEMENT from {display_name}: {message}'
    
    # Save to database
    save_message_to_db('📢 ANNOUNCEMENT', broadcast_text, is_system=1)
    
    # Broadcast to all users
    emit('new_message', {
        'id': 0,
        'sender': '📢 ANNOUNCEMENT',
        'text': broadcast_text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat')
    
    log_admin_action(email, 'broadcast', message[:100])
    logger.info(f'Admin broadcast sent by {email}')

@socketio.on('typing')
def handle_typing(data):
    """Handle typing indicator"""
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user_fast(email)
    if not user:
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        return
    
    # Check if typing indicator is enabled
    if get_cached_settings().get('enable_typing_indicator', 'true') != 'true':
        return
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    is_typing = bool(data.get('isTyping', False))
    
    # Only emit typing for active typers (throttled by client)
    if is_typing:
        emit('user_typing', {
            'user': display_name,
            'isTyping': True
        }, room='main_chat', include_self=False)

# ======================
# ERROR HANDLERS
# ======================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(429)
def ratelimit_error(error):
    return jsonify({'error': 'Too many requests. Please try again later.'}), 429

# ======================
# MAIN
# ======================

if __name__ == '__main__':
    # Initialize database
    init_db()
    
    # Get port from environment or use default
    port = int(os.environ.get('PORT', 10000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    logger.info("=" * 50)
    logger.info("✅ Chylnx Hub Server Ready!")
    logger.info(f"🔐 Admin: {os.environ.get('ADMIN_EMAIL', 'admin@chylnx.com')}")
    logger.info(f"⚠️  Change default admin password in production!")
    logger.info(f"🌐 Environment: {'Development' if debug else 'Production'}")
    logger.info("=" * 50)
    
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=debug,
        use_reloader=False,  # Disable reloader in production
        log_output=True
    )