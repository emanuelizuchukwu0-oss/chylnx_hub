from flask import Flask, request, jsonify, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import secrets
import database as db

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = secrets.token_hex(32)
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
        'isAdmin': bool(user['is_admin'])
    })

@app.route('/api/check-access', methods=['GET'])
def check_access():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({'hasAccess': False}), 401
    
    user = db.get_user_by_email(user_email)
    return jsonify({'hasAccess': bool(user['payment_verified']) if user else False})

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

if __name__ == '__main__':
    db.init_db()
    print("=" * 50)
    print("🚀 SERVER RUNNING!")
    print("Open: http://127.0.0.1:5000")
    print("Admin: admin@chylnx.com / admin123")
    print("=" * 50)
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)