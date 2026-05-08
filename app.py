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
from datetime import datetime
from functools import wraps

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True

CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Database path
DB_PATH = f'/tmp/chylnx_fresh_{int(time.time())}.db'
logger.info(f"📁 Database: {DB_PATH}")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def sanitize_input(text, max_length=500):
    if not text:
        return ''
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    return text[:max_length].strip()

def safe_get(row, key, default=None):
    if row is None:
        return default
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError, TypeError):
        return default

# ✅ CREATE DATABASE
logger.info("🔧 Creating database...")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
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
c.execute("INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?, ?, ?, ?, ?)",
          ('Administrator', 'admin@chylnx.com', admin_hash, 1, 1))

# Default settings
for key, val in [
    ('game_timer_hours','24'),('game_timer_minutes','0'),('game_timer_seconds','0'),
    ('weekly_timer_days','7'),('info_bar_text','Welcome!'),('info_bar_color','#667eea')
]:
    c.execute("INSERT OR REPLACE INTO settings (setting_key, setting_value) VALUES (?,?)", (key, val))

conn.commit()
conn.close()
logger.info("✅ Database ready!")

# ======================
# DECORATOR - Login Required
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
            return jsonify({'error': 'Admin access required'}), 403
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

@app.route('/api/debug', methods=['GET'])
def debug():
    conn = get_db()
    users = conn.execute("SELECT email, password_hash, is_admin, payment_verified FROM users").fetchall()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    return jsonify({
        'db_path': DB_PATH,
        'tables': [t['name'] for t in tables],
        'users': [dict(u) for u in users]
    })

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json(silent=True)
        logger.info(f"📝 Register: {data}")
        
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        full_name = sanitize_input(safe_get(data, 'fullName', ''), max_length=100)
        email = safe_get(data, 'email', '').lower().strip()
        password = safe_get(data, 'password', '')
        
        if not full_name or not email or not password:
            return jsonify({'error': 'All fields required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
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
        
        logger.info(f"✅ Registered: {email}")
        return jsonify({'success': True, 'message': 'Account created!'}), 201
        
    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json(silent=True)
        
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        email = safe_get(data, 'email', '').lower().strip()
        password = safe_get(data, 'password', '')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        
        if not user:
            conn.close()
            return jsonify({'error': 'Invalid email or password'}), 401
        
        input_hash = hash_password(password)
        
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
                'displayName': safe_get(user, 'display_name', None)
            }
        })
        
    except Exception as e:
        logger.error(f"Login error: {e}")
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
        'displayName': safe_get(user, 'display_name', None)
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

@app.route('/api/set-display-name', methods=['POST'])
def set_display_name():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Login required'}), 401
    
    data = request.get_json(silent=True)
    display_name = safe_get(data, 'displayName', '').strip()
    
    if len(display_name) < 2:
        return jsonify({'error': 'Name too short'}), 400
    
    conn = get_db()
    conn.execute("UPDATE users SET display_name = ? WHERE email = ?", (display_name, email))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'displayName': display_name})

@app.route('/api/submit-payment', methods=['POST'])
def submit_payment():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Login required'}), 401
    
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data'}), 400
    
    bank_name = safe_get(data, 'bankName', '').strip()
    reference = safe_get(data, 'reference', '').strip()
    method = safe_get(data, 'method', 'transfer')
    
    if not bank_name or not reference:
        return jsonify({'error': 'Bank name and reference required'}), 400
    
    conn = get_db()
    conn.execute(
        "INSERT INTO payments (user_email, bank_name, reference, payment_method) VALUES (?, ?, ?, ?)",
        (email, bank_name, reference, method)
    )
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

# ======================
# ✅ ADMIN ROUTES - FIXED
# ======================

@app.route('/api/admin/pending-payments', methods=['GET'])
def admin_pending_payments():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Please login first'}), 401
    
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email = ?", (email,)).fetchone()
    
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'Admin access required'}), 403
    
    # Get pending payments with user name
    payments = conn.execute("""
        SELECT p.*, u.full_name 
        FROM payments p 
        JOIN users u ON p.user_email = u.email 
        WHERE p.status = 'pending' 
        ORDER BY p.rowid DESC
    """).fetchall()
    conn.close()
    
    return jsonify({'payments': [dict(p) for p in payments]})

@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Please login first'}), 401
    
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email = ?", (email,)).fetchone()
    
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'Admin access required'}), 403
    
    users = conn.execute("""
        SELECT email, full_name, payment_verified, display_name 
        FROM users 
        WHERE is_admin = 0 
        ORDER BY rowid DESC
    """).fetchall()
    conn.close()
    
    return jsonify({'users': [dict(u) for u in users]})

@app.route('/api/admin/verify', methods=['POST'])
def admin_verify():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.get_json(silent=True)
    payment_id = safe_get(data, 'paymentId')
    
    if not payment_id:
        return jsonify({'error': 'Payment ID required'}), 400
    
    conn = get_db()
    
    # Check if admin
    admin = conn.execute("SELECT is_admin FROM users WHERE email = ?", (email,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'Admin access required'}), 403
    
    # Get payment
    payment = conn.execute("SELECT user_email FROM payments WHERE id = ? AND status = 'pending'", (payment_id,)).fetchone()
    
    if not payment:
        conn.close()
        return jsonify({'error': 'Payment not found or already processed'}), 404
    
    # Approve payment
    conn.execute("UPDATE payments SET status = 'approved' WHERE id = ?", (payment_id,))
    conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (payment['user_email'],))
    conn.commit()
    conn.close()
    
    logger.info(f"✅ Payment {payment_id} verified for {payment['user_email']}")
    return jsonify({'success': True, 'message': 'Payment verified!'})

@app.route('/api/admin/verify-user-payment', methods=['POST'])
def admin_verify_user():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.get_json(silent=True)
    target_email = safe_get(data, 'email', '').lower().strip()
    
    if not target_email:
        return jsonify({'error': 'User email required'}), 400
    
    conn = get_db()
    
    # Check if admin
    admin = conn.execute("SELECT is_admin FROM users WHERE email = ?", (email,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'Admin access required'}), 403
    
    # Verify user
    conn.execute("UPDATE users SET payment_verified = 1 WHERE email = ?", (target_email,))
    conn.commit()
    conn.close()
    
    logger.info(f"✅ User verified: {target_email}")
    return jsonify({'success': True, 'message': f'{target_email} verified!'})

@app.route('/api/admin/update-settings', methods=['POST'])
def admin_update_settings():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Please login first'}), 401
    
    data = request.get_json(silent=True)
    key = safe_get(data, 'key')
    value = safe_get(data, 'value')
    
    if not key:
        return jsonify({'error': 'Key required'}), 400
    
    conn = get_db()
    
    # Check if admin
    admin = conn.execute("SELECT is_admin FROM users WHERE email = ?", (email,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return jsonify({'error': 'Admin access required'}), 403
    
    conn.execute("UPDATE settings SET setting_value = ? WHERE setting_key = ?", (str(value), key))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

# Socket.IO
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
    
    text = safe_get(data, 'text', '').strip()
    if not text:
        conn.close()
        return
    
    display_name = safe_get(user, 'display_name', None)
    if not display_name:
        display_name = user['full_name'].split()[0] if user['full_name'] else 'User'
    
    if user['is_admin']:
        display_name = f'👑 {display_name}'
    
    conn.execute("INSERT INTO messages (sender_name, sender_email, message_text) VALUES (?, ?, ?)",
                 (display_name, email, text))
    conn.commit()
    conn.close()
    
    emit('new_message', {'sender': display_name, 'text': text, 'isSystem': False}, room='main_chat')

@socketio.on('admin_broadcast')
def handle_admin_broadcast(data):
    email = session.get('user_email')
    if not email:
        return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    
    if not user or not user['is_admin']:
        return
    
    message = safe_get(data, 'message', '').strip()
    if not message:
        return
    
    display_name = safe_get(user, 'display_name', None) or 'Admin'
    broadcast_text = f'🔊 ANNOUNCEMENT from {display_name}: {message}'
    
    conn = get_db()
    conn.execute("INSERT INTO messages (sender_name, sender_email, message_text, is_system) VALUES (?, ?, ?, 1)",
                 ('📢 ANNOUNCEMENT', email, broadcast_text))
    conn.commit()
    conn.close()
    
    emit('new_message', {
        'sender': '📢 ANNOUNCEMENT',
        'text': broadcast_text,
        'isSystem': True
    }, room='main_chat')

# ======================
# MAIN
# ======================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info("=" * 50)
    logger.info("🚀 Server starting...")
    logger.info(f"👑 Admin: admin@chylnx.com / admin123")
    logger.info("=" * 50)
    socketio.run(app, host='0.0.0.0', port=port, debug=True)