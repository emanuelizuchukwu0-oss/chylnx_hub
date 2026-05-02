from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import secrets
import database as db
import os

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
CORS(app, supports_credentials=True)

socketio = SocketIO(app, cors_allowed_origins='*')

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
    data = request.json
    full_name = data.get('fullName', '').strip()
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    referral_code = data.get('referralCode', '').strip()
    
    if not full_name or not email or not password:
        return jsonify({'error': 'All fields required'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Password must be 6+ characters'}), 400
    
    if referral_code:
        ref = db.validate_referral_code(referral_code)
        if not ref:
            return jsonify({'error': 'Invalid referral code'}), 400
    
    success = db.create_user(full_name, email, password, referral_code if referral_code else None)
    if not success:
        return jsonify({'error': 'Email already registered'}), 400
    
    return jsonify({'success': True})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    
    user = db.authenticate_user(email, password)
    if not user:
        return jsonify({'error': 'Invalid email or password'}), 401
    
    session['user_email'] = user['email']
    
    return jsonify({
        'success': True,
        'user': {
            'email': user['email'],
            'fullName': user['full_name'],
            'paymentVerified': bool(user['payment_verified']),
            'isAdmin': bool(user['is_admin'])
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.pop('user_email', None)
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
def get_current_user():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = db.get_user_by_email(user_email)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'email': user['email'],
        'fullName': user['full_name'],
        'paymentVerified': bool(user['payment_verified']),
        'isAdmin': bool(user['is_admin']),
        'displayName': user.get('display_name')
    })

@app.route('/api/check-access', methods=['GET'])
def check_access():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({'hasAccess': False}), 401
    
    user = db.get_user_by_email(user_email)
    return jsonify({'hasAccess': bool(user['payment_verified']) if user else False})

@app.route('/api/set-display-name', methods=['POST'])
def set_display_name():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    display_name = data.get('displayName', '').strip()
    
    if not display_name or len(display_name) < 2 or len(display_name) > 20:
        return jsonify({'error': 'Name must be 2-20 characters'}), 400
    
    db.update_user_display_name(user_email, display_name)
    return jsonify({'success': True, 'displayName': display_name})

@app.route('/api/submit-payment', methods=['POST'])
def submit_payment():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    bank_name = data.get('bankName', '').strip()
    reference = data.get('reference', '').strip()
    payment_method = data.get('method', 'transfer')
    
    if not bank_name or not reference:
        return jsonify({'error': 'Missing fields'}), 400
    
    user = db.get_user_by_email(user_email)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    if user['payment_verified']:
        return jsonify({'error': 'Already verified'}), 400
    
    db.create_payment_request(user_email, user['full_name'], bank_name, reference, payment_method)
    return jsonify({'success': True, 'message': 'Payment submitted'})

@app.route('/api/messages', methods=['GET'])
def get_messages():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({'error': 'Not logged in'}), 401
    
    user = db.get_user_by_email(user_email)
    if not user or not user['payment_verified']:
        return jsonify({'error': 'Payment required'}), 403
    
    messages = db.get_recent_messages(100)
    return jsonify({'messages': messages})

@app.route('/api/online-count', methods=['GET'])
def get_online_count():
    count = db.get_online_count()
    return jsonify({'onlineCount': count})

# ==================== ADMIN ROUTES ====================

@app.route('/api/admin/pending-payments', methods=['GET'])
def admin_pending():
    user_email = session.get('user_email')
    if not user_email or not db.is_admin(user_email):
        return jsonify({'error': 'Unauthorized'}), 401
    
    payments = db.get_pending_payments()
    return jsonify({'payments': payments})

@app.route('/api/admin/verify', methods=['POST'])
def admin_verify():
    user_email = session.get('user_email')
    if not user_email or not db.is_admin(user_email):
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    payment_id = data.get('paymentId')
    
    if db.approve_payment(payment_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Payment not found'}), 404

@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    user_email = session.get('user_email')
    if not user_email or not db.is_admin(user_email):
        return jsonify({'error': 'Unauthorized'}), 401
    
    users = db.get_all_users()
    return jsonify({'users': users})

@app.route('/api/admin/verify-user-payment', methods=['POST'])
def admin_verify_user():
    user_email = session.get('user_email')
    if not user_email or not db.is_admin(user_email):
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    target_email = data.get('email', '').lower().strip()
    db.verify_user_payment(target_email)
    return jsonify({'success': True})

@app.route('/api/admin/referral-codes', methods=['GET'])
def admin_referral_codes():
    user_email = session.get('user_email')
    if not user_email or not db.is_admin(user_email):
        return jsonify({'error': 'Unauthorized'}), 401
    
    codes = db.get_all_referral_codes()
    return jsonify({'codes': codes})

# ==================== SOCKET.IO EVENTS ====================

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')

@socketio.on('join_chat')
def handle_join_chat(data):
    user_email = session.get('user_email')
    if not user_email:
        emit('error', {'message': 'Not authenticated'})
        return
    
    user = db.get_user_by_email(user_email)
    if not user or not user['payment_verified']:
        emit('error', {'message': 'Access denied - Payment required'})
        return
    
    display_name = user.get('display_name', user['full_name'].split()[0])
    
    join_room('main_chat')
    db.set_user_online(user_email, request.sid)
    
    messages = db.get_recent_messages(100)
    emit('chat_history', {'messages': messages})
    
    online_count = db.get_online_count()
    emit('online_count', {'count': online_count}, room='main_chat')
    
    system_msg = db.save_message('System', f'{display_name} joined the chat', is_system=True)
    emit('new_message', {
        'id': system_msg['id'],
        'sender': 'System',
        'text': f'{display_name} joined the chat',
        'timestamp': system_msg['timestamp'],
        'isSystem': True
    }, room='main_chat')

@socketio.on('send_message')
def handle_send_message(data):
    user_email = session.get('user_email')
    if not user_email:
        return
    
    user = db.get_user_by_email(user_email)
    if not user or not user['payment_verified']:
        emit('error', {'message': 'Access denied'})
        return
    
    message_text = data.get('text', '').strip()
    if not message_text or len(message_text) > 500:
        return
    
    display_name = user.get('display_name', user['full_name'].split()[0])
    
    saved_msg = db.save_message(display_name, message_text, user_email)
    
    emit('new_message', {
        'id': saved_msg['id'],
        'sender': display_name,
        'text': message_text,
        'timestamp': saved_msg['timestamp'],
        'isSystem': False
    }, room='main_chat')

@socketio.on('heartbeat')
def handle_heartbeat():
    user_email = session.get('user_email')
    if user_email:
        db.heartbeat(user_email)
        new_count = db.get_online_count()
        emit('online_count', {'count': new_count}, room='main_chat')

@socketio.on('typing')
def handle_typing(data):
    user_email = session.get('user_email')
    if not user_email:
        return
    
    user = db.get_user_by_email(user_email)
    if user and user.get('display_name'):
        emit('user_typing', {
            'user': user['display_name'],
            'isTyping': data.get('isTyping', False)
        }, room='main_chat', include_self=False)

# Initialize database
db.init_db()

print("=" * 50)
print("🚀 CHYLNX HUB SERVER RUNNING!")
print("=" * 50)

# This is for Gunicorn on Render
app = app
socketio = socketio

# This is for local testing
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000)