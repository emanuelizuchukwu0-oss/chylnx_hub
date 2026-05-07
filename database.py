from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import eventlet
eventlet.monkey_patch()
import secrets
import os
import re
import time
import logging
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock

# ======================
# IMPORT DATABASE MODULE
# ======================
from database import (
    init_db, get_db, hash_password, get_user_by_email, create_user,
    authenticate_user, verify_user_payment, create_payment_request,
    get_pending_payments, approve_payment, save_message, get_recent_messages,
    get_all_users, update_user_display_name, is_admin, get_online_count,
    validate_referral_code
)

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
app.config['SESSION_COOKIE_DOMAIN'] = None
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
    manage_session=False
)

# Settings cache
settings_cache = {}
settings_cache_time = 0
CACHE_TTL = 300

# Validation patterns
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
USERNAME_REGEX = re.compile(r'^[a-zA-Z0-9\s\-_]{2,50}$')

# ======================
# UTILITY FUNCTIONS
# ======================

def validate_email(email):
    return bool(EMAIL_REGEX.match(email))

def validate_username(name):
    return bool(USERNAME_REGEX.match(name))

def sanitize_input(text, max_length=500):
    if not text:
        return ''
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    return text[:max_length].strip()

def get_cached_settings(force_refresh=False):
    global settings_cache, settings_cache_time
    now = time.time()
    
    if force_refresh or (now - settings_cache_time) > CACHE_TTL or not settings_cache:
        try:
            conn = get_db()
            rows = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
            settings_cache = {row['setting_key']: row['setting_value'] for row in rows}
            settings_cache_time = now
            conn.close()
        except Exception as e:
            logger.error(f"Settings cache error: {e}")
            return settings_cache if settings_cache else {}
            
    return settings_cache

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
        
        if not is_admin(session['user_email']):
            return jsonify({'error': 'Admin privileges required'}), 403
            
        return f(*args, **kwargs)
    return decorated_function

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
        referral_code = data.get('referralCode', '').strip() or None
        
        # Validation
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
        
        if not validate_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
            
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
            
        if len(full_name) < 2:
            return jsonify({'error': 'Name must be at least 2 characters'}), 400
        
        # Validate referral code if provided
        if referral_code:
            ref = validate_referral_code(referral_code)
            if not ref:
                return jsonify({'error': 'Invalid or expired referral code'}), 400
        
        # Create user using database.py function
        success = create_user(full_name, email, password, referral_code)
        
        if success:
            logger.info(f"New user registered: {email}")
            return jsonify({'success': True, 'message': 'Registration successful'}), 201
        else:
            return jsonify({'error': 'Email already registered'}), 409
            
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
        
        # Authenticate using database.py function
        user = authenticate_user(email, password)
        
        if not user:
            return jsonify({'error': 'Invalid email or password'}), 401
        
        # Set session
        session.clear()
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
    email = session.get('user_email')
    session.clear()
    if email:
        logger.info(f"User logged out: {email}")
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
@login_required
def me():
    user = get_user_by_email(session['user_email'])
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
        
        update_user_display_name(session['user_email'], display_name)
        
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
    user = get_user_by_email(session['user_email'])
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
        payment_method = sanitize_input(data.get('method', 'transfer'), max_length=50)
        
        if not bank_name or not reference:
            return jsonify({'error': 'Bank name and reference are required'}), 400
        
        user = get_user_by_email(session['user_email'])
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Create payment using database.py function
        payment_id = create_payment_request(
            session['user_email'],
            user['full_name'],
            bank_name,
            reference,
            payment_method
        )
        
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
    settings = get_cached_settings()
    safe_settings = {k: v for k, v in settings.items() 
                     if not k.startswith('admin_') and not k.startswith('secret_')}
    return jsonify({'settings': safe_settings})

# ======================
# ROUTES - Admin
# ======================

@app.route('/api/admin/update-settings', methods=['POST'])
@admin_required
def update_settings():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        setting_key = sanitize_input(data.get('key', ''), max_length=50)
        setting_value = str(data.get('value', ''))
        
        if not setting_key:
            return jsonify({'error': 'Setting key required'}), 400
        
        allowed_settings = {
            'game_timer_hours', 'game_timer_minutes', 'game_timer_seconds',
            'weekly_timer_days', 'weekly_timer_hours', 'weekly_timer_minutes',
            'weekly_timer_seconds', 'info_bar_text', 'info_bar_color',
            'max_message_length', 'chat_history_limit', 'enable_typing_indicator'
        }
        
        if setting_key not in allowed_settings:
            return jsonify({'error': 'Invalid setting key'}), 400
        
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (setting_key, setting_value) VALUES (?, ?)",
            (setting_key, setting_value)
        )
        conn.commit()
        conn.close()
        
        global settings_cache_time
        settings_cache_time = 0
        
        logger.info(f"Setting updated by {session['user_email']}: {setting_key}")
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        return jsonify({'error': 'Failed to update settings'}), 500

@app.route('/api/admin/pending-payments', methods=['GET'])
@admin_required
def admin_pending_payments():
    try:
        payments = get_pending_payments()
        return jsonify({'payments': payments})
    except Exception as e:
        logger.error(f"Pending payments error: {e}")
        return jsonify({'error': 'Failed to load payments'}), 500

@app.route('/api/admin/verify', methods=['POST'])
@admin_required
def verify_payment():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        payment_id = data.get('paymentId')
        if not payment_id:
            return jsonify({'error': 'Payment ID required'}), 400
        
        success = approve_payment(payment_id)
        
        if success:
            logger.info(f"Payment verified by {session['user_email']}: {payment_id}")
            return jsonify({'success': True, 'message': 'Payment verified successfully'})
        else:
            return jsonify({'error': 'Payment not found or already processed'}), 404
        
    except Exception as e:
        logger.error(f"Verify payment error: {e}")
        return jsonify({'error': 'Failed to verify payment'}), 500

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_users():
    try:
        users = get_all_users()
        return jsonify({'users': users})
    except Exception as e:
        logger.error(f"List users error: {e}")
        return jsonify({'error': 'Failed to load users'}), 500

@app.route('/api/admin/verify-user-payment', methods=['POST'])
@admin_required
def verify_user():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        target_email = data.get('email', '').lower().strip()
        
        if not validate_email(target_email):
            return jsonify({'error': 'Invalid email'}), 400
        
        verify_user_payment(target_email)
        
        logger.info(f"User manually verified by {session['user_email']}: {target_email}")
        return jsonify({'success': True, 'message': f'{target_email} verified'})
        
    except Exception as e:
        logger.error(f"Verify user error: {e}")
        return jsonify({'error': 'Failed to verify user'}), 500

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    try:
        conn = get_db()
        total_users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()['count']
        verified_users = conn.execute(
            "SELECT COUNT(*) as count FROM users WHERE payment_verified = 1 OR is_admin = 1"
        ).fetchone()['count']
        pending_payments = conn.execute(
            "SELECT COUNT(*) as count FROM payments WHERE status = 'pending'"
        ).fetchone()['count']
        total_messages = conn.execute("SELECT COUNT(*) as count FROM messages").fetchone()['count']
        conn.close()
        
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
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    email = session.get('user_email')
    
    with ACTIVE_USERS_LOCK:
        if email and email in active_users:
            del active_users[email]
            count = len(active_users)
        else:
            count = len(active_users)
    
    logger.info(f"Client disconnected: {request.sid}")
    socketio.emit('online_count', {'count': count}, room='main_chat')

@socketio.on('join_chat')
def handle_join_chat():
    email = session.get('user_email')
    
    if not email:
        emit('error', {'message': 'Authentication required'})
        return
    
    user = get_user_by_email(email)
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
    
    # Send chat history
    settings = get_cached_settings()
    history_limit = int(settings.get('chat_history_limit', 50))
    messages = get_recent_messages(history_limit)
    emit('chat_history', {'messages': messages})
    
    # Update online count
    emit('online_count', {'count': online_count}, room='main_chat')
    
    # System notification
    if not user['is_admin']:
        join_msg = f'{display_name} joined the chat'
        save_message('System', join_msg, is_system=True)
        emit('new_message', {
            'id': 0,
            'sender': 'System',
            'text': join_msg,
            'timestamp': datetime.now().isoformat(),
            'isSystem': True
        }, room='main_chat', skip_sid=request.sid)

@socketio.on('send_message')
def handle_send_message(data):
    email = session.get('user_email')
    
    if not email:
        emit('error', {'message': 'Authentication required'})
        return
    
    user = get_user_by_email(email)
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
    
    # Rate limiting
    with ACTIVE_USERS_LOCK:
        if email in active_users:
            last_msg_time = active_users[email].get('last_message_time', 0)
            if time.time() - last_msg_time < 0.5:
                emit('error', {'message': 'Please wait before sending another message'})
                return
            active_users[email]['last_message_time'] = time.time()
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    # Save message using database.py
    message = save_message(display_name, message_text, sender_email=email, is_system=False)
    
    if message:
        message_data = {
            'id': message['id'],
            'sender': display_name,
            'text': message_text,
            'timestamp': datetime.now().isoformat(),
            'isSystem': False
        }
        emit('new_message', message_data, room='main_chat')

@socketio.on('admin_broadcast')
def handle_admin_broadcast(data):
    email = session.get('user_email')
    
    if not email:
        emit('error', {'message': 'Authentication required'})
        return
    
    user = get_user_by_email(email)
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
    
    save_message('📢 ANNOUNCEMENT', broadcast_text, is_system=True)
    
    emit('new_message', {
        'id': 0,
        'sender': '📢 ANNOUNCEMENT',
        'text': broadcast_text,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat')
    
    logger.info(f'Admin broadcast sent by {email}')

@socketio.on('typing')
def handle_typing(data):
    email = session.get('user_email')
    if not email:
        return
    
    user = get_user_by_email(email)
    if not user:
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        return
    
    display_name = user.get('display_name') or user['full_name'].split()[0]
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    is_typing = bool(data.get('isTyping', False))
    
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
        use_reloader=False,
        log_output=True
    )