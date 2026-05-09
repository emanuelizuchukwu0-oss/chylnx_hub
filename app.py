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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

CORS(app, supports_credentials=True, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

DB_PATH = f'/tmp/chat_{int(time.time())}.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def sanitize_input(text, max_length=500):
    if not text: return ''
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))[:max_length].strip()

def safe_get(row, key, default=None):
    if row is None: return default
    try:
        val = row[key]
        return val if val is not None else default
    except: return default

# Create database
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT, email TEXT UNIQUE, password_hash TEXT,
    payment_verified INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, display_name TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT, bank_name TEXT, reference TEXT,
    payment_method TEXT DEFAULT 'transfer', status TEXT DEFAULT 'pending'
)''')
c.execute('''CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_name TEXT, sender_email TEXT, message_text TEXT, is_system INTEGER DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
c.execute('''CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY, setting_value TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    winner_email TEXT, winner_name TEXT,
    account_name TEXT, account_number TEXT, bank_name TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

c.execute("INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified) VALUES (?,?,?,?,?)",
          ('Admin', 'admin@chylnx.com', hash_password('admin123'), 1, 1))

for k,v in [('game_timer_hours','24'),('info_bar_text','Welcome!'),('info_bar_color','#667eea')]:
    c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k,v))

conn.commit()
conn.close()

# ======================
# ROUTES
# ======================

@app.route('/')
def index(): return send_from_directory('.', 'login.html')

@app.route('/<path:filename>')
def serve(filename):
    if '..' in filename: return jsonify({'error':'Invalid'}), 400
    return send_from_directory('.', filename)

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True)
    if not data: return jsonify({'error':'No data'}), 400
    name = sanitize_input(safe_get(data,'fullName',''),100)
    email = safe_get(data,'email','').lower().strip()
    pwd = safe_get(data,'password','')
    if not name or not email or not pwd: return jsonify({'error':'All fields required'}), 400
    if len(pwd) < 6: return jsonify({'error':'Password too short'}), 400
    conn = get_db()
    if conn.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone():
        conn.close(); return jsonify({'error':'Email already registered'}), 409
    conn.execute("INSERT INTO users (full_name,email,password_hash) VALUES (?,?,?)",(name,email,hash_password(pwd)))
    conn.commit(); conn.close()
    return jsonify({'success':True,'message':'Account created!'}), 201

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True)
    if not data: return jsonify({'error':'No data'}), 400
    email = safe_get(data,'email','').lower().strip()
    pwd = safe_get(data,'password','')
    if not email or not pwd: return jsonify({'error':'Email and password required'}), 400
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not user or user['password_hash'] != hash_password(pwd):
        conn.close(); return jsonify({'error':'Invalid credentials'}), 401
    conn.close()
    session.clear()
    session['user_email'] = user['email']
    session['user_id'] = user['id']
    session.permanent = True
    logger.info(f"✅ Login: {email}")
    return jsonify({'success':True,'user':{
        'email':user['email'],'fullName':user['full_name'],
        'paymentVerified':bool(user['payment_verified']),'isAdmin':bool(user['is_admin']),
        'displayName':safe_get(user,'display_name')
    }})

@app.route('/api/auth/me', methods=['GET'])
def me():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Not logged in'}), 401
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    conn.close()
    if not user: return jsonify({'error':'Not found'}), 404
    return jsonify({
        'email':user['email'],'fullName':user['full_name'],
        'paymentVerified':bool(user['payment_verified']),'isAdmin':bool(user['is_admin']),
        'displayName':safe_get(user,'display_name')
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear(); return jsonify({'success':True})

@app.route('/api/settings', methods=['GET'])
def settings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM settings").fetchall()
    conn.close()
    return jsonify({'settings':{r['setting_key']:r['setting_value'] for r in rows}})

@app.route('/api/check-access', methods=['GET'])
def check_access():
    email = session.get('user_email')
    if not email: return jsonify({'hasAccess':False}), 401
    conn = get_db()
    u = conn.execute("SELECT payment_verified,is_admin FROM users WHERE email=?",(email,)).fetchone()
    conn.close()
    return jsonify({'hasAccess':bool(u['payment_verified'] or u['is_admin']) if u else False})

@app.route('/api/set-display-name', methods=['POST'])
def set_name():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login required'}), 401
    data = request.get_json(silent=True)
    name = safe_get(data,'displayName','').strip()
    if len(name)<2: return jsonify({'error':'Too short'}), 400
    conn = get_db()
    conn.execute("UPDATE users SET display_name=? WHERE email=?",(name,email))
    conn.commit(); conn.close()
    return jsonify({'success':True})

@app.route('/api/submit-payment', methods=['POST'])
def submit_payment():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login required'}), 401
    data = request.get_json(silent=True)
    bank = safe_get(data,'bankName','').strip()
    ref = safe_get(data,'reference','').strip()
    method = safe_get(data,'method','transfer')
    if not bank or not ref: return jsonify({'error':'Bank and reference required'}), 400
    conn = get_db()
    conn.execute("INSERT INTO payments (user_email,bank_name,reference,payment_method) VALUES (?,?,?,?)",(email,bank,ref,method))
    conn.commit(); conn.close()
    return jsonify({'success':True})

# Admin routes
@app.route('/api/admin/pending-payments')
def admin_payments():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login'}), 401
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return jsonify({'error':'Admin only'}), 403
    payments = conn.execute("SELECT p.*,u.full_name FROM payments p JOIN users u ON p.user_email=u.email WHERE p.status='pending' ORDER BY p.rowid DESC").fetchall()
    conn.close()
    return jsonify({'payments':[dict(p) for p in payments]})

@app.route('/api/admin/users')
def admin_users():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login'}), 401
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return jsonify({'error':'Admin only'}), 403
    users = conn.execute("SELECT email,full_name,payment_verified,display_name FROM users WHERE is_admin=0 ORDER BY rowid DESC").fetchall()
    conn.close()
    return jsonify({'users':[dict(u) for u in users]})

@app.route('/api/admin/verify', methods=['POST'])
def admin_verify():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login'}), 401
    data = request.get_json(silent=True)
    pid = safe_get(data,'paymentId')
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return jsonify({'error':'Admin only'}), 403
    p = conn.execute("SELECT user_email FROM payments WHERE id=? AND status='pending'",(pid,)).fetchone()
    if not p: conn.close(); return jsonify({'error':'Not found'}), 404
    conn.execute("UPDATE payments SET status='approved' WHERE id=?",(pid,))
    conn.execute("UPDATE users SET payment_verified=1 WHERE email=?",(p['user_email'],))
    conn.commit(); conn.close()
    return jsonify({'success':True})

@app.route('/api/admin/verify-user-payment', methods=['POST'])
def admin_verify_user():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login'}), 401
    data = request.get_json(silent=True)
    target = safe_get(data,'email','').lower().strip()
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return jsonify({'error':'Admin only'}), 403
    conn.execute("UPDATE users SET payment_verified=1 WHERE email=?",(target,))
    conn.commit(); conn.close()
    return jsonify({'success':True})

@app.route('/api/admin/update-settings', methods=['POST'])
def admin_settings():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login'}), 401
    data = request.get_json(silent=True)
    k = safe_get(data,'key'); v = safe_get(data,'value')
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return jsonify({'error':'Admin only'}), 403
    conn.execute("UPDATE settings SET setting_value=? WHERE setting_key=?",(str(v),k))
    conn.commit(); conn.close()
    return jsonify({'success':True})

@app.route('/api/admin/online-users', methods=['GET'])
def get_online_users():
    email = session.get('user_email')
    if not email: return jsonify({'error':'Login'}), 401
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return jsonify({'error':'Admin only'}), 403
    online_list = []
    for user_email, data in online_users.items():
        if user_email != email:
            online_list.append({'email': user_email, 'name': data.get('name', 'Unknown')})
    conn.close()
    return jsonify({'online_users': online_list, 'count': len(online_list)})

# ======================
# SOCKET.IO EVENTS
# ======================

online_users = {}

@socketio.on('connect')
def on_connect():
    logger.info(f"🟢 Connected: {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    to_remove = None
    for email, data in online_users.items():
        if data['sid'] == request.sid:
            to_remove = email
            break
    if to_remove:
        del online_users[to_remove]
        logger.info(f"🔴 Disconnected: {to_remove}")
        socketio.emit('online_count', {'count': len(online_users)}, room='main_chat')

@socketio.on('join_chat')
def on_join():
    email = session.get('user_email')
    logger.info(f"💬 Join: {email}")
    
    if not email:
        emit('error', {'message': 'Please login first'})
        return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    conn.close()
    
    if not user:
        emit('error', {'message': 'User not found'})
        return
    
    if not user['payment_verified'] and not user['is_admin']:
        emit('error', {'message': 'Payment required'})
        return
    
    name = safe_get(user,'display_name') or user['full_name'].split()[0]
    online_users[email] = {'sid': request.sid, 'name': name}
    
    join_room('main_chat')
    logger.info(f"✅ {email} joined. Online: {len(online_users)}")
    
    # Send chat history
    conn = get_db()
    msgs = conn.execute("SELECT * FROM messages ORDER BY rowid DESC LIMIT 50").fetchall()
    conn.close()
    
    formatted = []
    for m in reversed(msgs):
        formatted.append({
            'id': m['id'],
            'sender': m['sender_name'],
            'text': m['message_text'],
            'timestamp': m['timestamp'] if m['timestamp'] else datetime.now().isoformat(),
            'isSystem': bool(m['is_system']),
            'senderEmail': m['sender_email'] if 'sender_email' in m.keys() else ''
        })
    
    emit('chat_history', {'messages': formatted})
    socketio.emit('online_count', {'count': len(online_users)}, room='main_chat')

@socketio.on('send_message')
def on_message(data):
    email = session.get('user_email')
    if not email: return
    
    text = safe_get(data, 'text', '').strip()
    if not text: return
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not user: conn.close(); return
    
    if not user['payment_verified'] and not user['is_admin']:
        conn.close(); return
    
    name = safe_get(user,'display_name') or user['full_name'].split()[0]
    if user['is_admin']: name = f'👑 {name}'
    
    conn.execute("INSERT INTO messages (sender_name,sender_email,message_text,is_system,timestamp) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                 (name,email,text,0))
    conn.commit()
    msg = conn.execute("SELECT * FROM messages WHERE rowid=last_insert_rowid()").fetchone()
    conn.close()
    
    logger.info(f"📩 {name}: {text[:30]}")
    
    emit('new_message', {
        'id': msg['id'],
        'sender': name,
        'text': text,
        'timestamp': msg['timestamp'] if msg['timestamp'] else datetime.now().isoformat(),
        'isSystem': False,
        'senderEmail': email
    }, room='main_chat')

@socketio.on('admin_broadcast')
def on_broadcast(data):
    email = session.get('user_email')
    if not email: return
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not user or not user['is_admin']: conn.close(); return
    msg_text = safe_get(data,'message','').strip()
    if not msg_text: conn.close(); return
    name = safe_get(user,'display_name') or 'Admin'
    txt = f'🔊 {name}: {msg_text}'
    conn.execute("INSERT INTO messages (sender_name,sender_email,message_text,is_system,timestamp) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                 ('📢 ANNOUNCEMENT',email,txt,1))
    conn.commit(); conn.close()
    emit('new_message', {'id':0,'sender':'📢 ANNOUNCEMENT','text':txt,'timestamp':datetime.now().isoformat(),'isSystem':True,'senderEmail':email}, room='main_chat')

@socketio.on('declare_winner')
def on_declare_winner(data):
    email = session.get('user_email')
    if not email: return
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?",(email,)).fetchone()
    if not admin or not admin['is_admin']: conn.close(); return
    winner_name = safe_get(data, 'name', 'Winner')
    winner_email = safe_get(data, 'email', '')
    win_msg = f'🏆🎉 {winner_name} is the WINNER! 🎉🏆'
    conn.execute("INSERT INTO messages (sender_name,sender_email,message_text,is_system,timestamp) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                 ('🏆 SYSTEM', email, win_msg, 1))
    conn.commit(); conn.close()
    emit('winner_announced', {'winner_email': winner_email, 'winner_name': winner_name}, room='main_chat')
    emit('new_message', {'id':0,'sender':'🏆 SYSTEM','text':win_msg,'timestamp':datetime.now().isoformat(),'isSystem':True,'senderEmail':email}, room='main_chat')
    logger.info(f'🏆 Winner: {winner_name}')

@socketio.on('submit_claim')
def on_submit_claim(data):
    email = session.get('user_email')
    if not email: return
    
    account_name = safe_get(data, 'accountName', '')
    account_number = safe_get(data, 'accountNumber', '')
    bank_name = safe_get(data, 'bankName', '')
    winner_name = safe_get(data, 'winnerName', '')
    winner_email = safe_get(data, 'winnerEmail', email)
    
    # Save to database
    conn = get_db()
    conn.execute("INSERT INTO claims (winner_email, winner_name, account_name, account_number, bank_name) VALUES (?,?,?,?,?)",
                 (winner_email, winner_name, account_name, account_number, bank_name))
    conn.commit()
    conn.close()
    
    claim_msg = f'💰 CLAIM: {winner_name} | Bank: {bank_name} | Acct: {account_number} | Name: {account_name}'
    
    # Save to messages table
    conn = get_db()
    conn.execute("INSERT INTO messages (sender_name,sender_email,message_text,is_system,timestamp) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                 ('💰 CLAIM SYSTEM', winner_email, claim_msg, 1))
    conn.commit()
    conn.close()
    
    # ✅ Send confirmation to the winner only
    emit('claim_response', {'success': True})
    
    # ✅ Send claim details ONLY to admin (find admin's socket)
    for admin_email, admin_data in online_users.items():
        conn = get_db()
        admin = conn.execute("SELECT is_admin FROM users WHERE email=?", (admin_email,)).fetchone()
        conn.close()
        if admin and admin['is_admin']:
            # Send claim message ONLY to this admin's socket
            socketio.emit('new_message', {
                'id': 0,
                'sender': '💰 CLAIM SYSTEM',
                'text': claim_msg,
                'timestamp': datetime.now().isoformat(),
                'isSystem': True,
                'senderEmail': winner_email
            }, room=admin_data['sid'])  # ✅ Send to admin's specific room
            
            # Also send as a special claim notification
            socketio.emit('claim_notification', {
                'winner_name': winner_name,
                'winner_email': winner_email,
                'account_name': account_name,
                'account_number': account_number,
                'bank_name': bank_name,
                'message': claim_msg
            }, room=admin_data['sid'])
    
    logger.info(f'💰 Claim from {winner_name} sent to admin only')

@socketio.on('close_chat_session')
def on_close_chat():
    email = session.get('user_email')
    if not email: return
    
    # Verify sender is admin
    conn = get_db()
    admin = conn.execute("SELECT is_admin FROM users WHERE email=?", (email,)).fetchone()
    if not admin or not admin['is_admin']:
        conn.close()
        return
    conn.close()
    
    # Save system message
    close_msg = '🔒 All winners have been rewarded! Chat session is now closed.'
    conn = get_db()
    conn.execute("INSERT INTO messages (sender_name,sender_email,message_text,is_system,timestamp) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                 ('🔒 SYSTEM', email, close_msg, 1))
    conn.commit()
    conn.close()
    
    # Send close signal to ALL users in main_chat
    emit('chat_closed', {
        'message': '🏆 All winners have been rewarded and the session has closed! 🏆\n\nRedirecting to homepage...'
    }, room='main_chat')
    
    # Also send as system message
    emit('new_message', {
        'id': 0,
        'sender': '🔒 SYSTEM',
        'text': close_msg,
        'timestamp': datetime.now().isoformat(),
        'isSystem': True
    }, room='main_chat')
    
    logger.info(f'🔒 Chat closed by admin: {email}')
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info("=" * 50)
    logger.info("🚀 Server starting on port " + str(port))
    logger.info("👑 Admin: admin@chylnx.com / admin123")
    logger.info("=" * 50)
    socketio.run(app, host='0.0.0.0', port=port, debug=True)