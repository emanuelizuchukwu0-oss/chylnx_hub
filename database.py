import sqlite3
import hashlib
import os

# Use a persistent location on Render
DB_PATH = '/opt/render/project/src/data/chylnx.db'

# Create data directory if it doesn't exist
os.makedirs('/opt/render/project/src/data', exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hashed):
    return hash_password(password) == hashed

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            payment_verified BOOLEAN DEFAULT 0,
            referral_code_used TEXT,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Payments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            bank_name TEXT NOT NULL,
            reference TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            amount INTEGER DEFAULT 2500,
            status TEXT DEFAULT 'pending',
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_name TEXT NOT NULL,
            message_text TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_system BOOLEAN DEFAULT 0
        )
    ''')
    
    # Referral codes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referral_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            uses_remaining INTEGER DEFAULT 5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default referral codes
    cursor.execute('SELECT * FROM referral_codes WHERE code = "WELCOME2024"')
    if not cursor.fetchone():
        cursor.execute('INSERT INTO referral_codes (code, uses_remaining) VALUES ("WELCOME2024", 100)')
    
    # Create admin user
    cursor.execute('SELECT * FROM users WHERE email = "admin@chylnx.com"')
    if not cursor.fetchone():
        admin_password = hash_password('admin123')
        cursor.execute('''
            INSERT INTO users (full_name, email, password_hash, is_admin, payment_verified)
            VALUES (?, ?, ?, ?, ?)
        ''', ('Administrator', 'admin@chylnx.com', admin_password, 1, 1))
    
    conn.commit()
    conn.close()
    print("✅ Database initialized!")

# ==================== USER FUNCTIONS ====================

def create_user(full_name, email, password, referral_code=None):
    conn = get_db()
    cursor = conn.cursor()
    password_hash = hash_password(password)
    
    try:
        cursor.execute('''
            INSERT INTO users (full_name, email, password_hash, referral_code_used)
            VALUES (?, ?, ?, ?)
        ''', (full_name, email.lower(), password_hash, referral_code))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def authenticate_user(email, password):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE email = ?', (email.lower(),))
    user = cursor.fetchone()
    conn.close()
    
    if user and check_password(password, user['password_hash']):
        return dict(user)
    return None

def get_user_by_email(email):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE email = ?', (email.lower(),))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def get_all_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, full_name, email, payment_verified FROM users WHERE is_admin = 0')
    users = cursor.fetchall()
    conn.close()
    return [dict(user) for user in users]

def update_user_display_name(email, display_name):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET display_name = ? WHERE email = ?', (display_name, email.lower()))
    conn.commit()
    conn.close()

def verify_user_payment(email):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET payment_verified = 1 WHERE email = ?', (email.lower(),))
    conn.commit()
    conn.close()

def is_admin(email):
    user = get_user_by_email(email)
    return user['is_admin'] == 1 if user else False

def create_payment_request(user_email, full_name, bank_name, reference, payment_method):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO payments (user_email, full_name, bank_name, reference, payment_method)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_email.lower(), full_name, bank_name, reference, payment_method))
    conn.commit()
    payment_id = cursor.lastrowid
    conn.close()
    return payment_id

def get_pending_payments():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM payments WHERE status = "pending" ORDER BY submitted_at DESC')
    payments = cursor.fetchall()
    conn.close()
    return [dict(p) for p in payments]

def approve_payment(payment_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT user_email FROM payments WHERE id = ?', (payment_id,))
    payment = cursor.fetchone()
    
    if payment:
        cursor.execute('UPDATE payments SET status = "approved" WHERE id = ?', (payment_id,))
        cursor.execute('UPDATE users SET payment_verified = 1 WHERE email = ?', (payment['user_email'],))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

def save_message(sender_name, message_text, is_system=False):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO messages (sender_name, message_text, is_system, timestamp)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (sender_name, message_text, is_system))
    conn.commit()
    message_id = cursor.lastrowid
    cursor.execute('SELECT * FROM messages WHERE id = ?', (message_id,))
    message = cursor.fetchone()
    conn.close()
    return dict(message)

def get_recent_messages(limit=100):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?', (limit,))
    messages = cursor.fetchall()
    conn.close()
    return [dict(m) for m in reversed(messages)]

def validate_referral_code(code):
    if not code:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM referral_codes WHERE code = ? AND uses_remaining > 0', (code.upper(),))
    ref = cursor.fetchone()
    conn.close()
    return dict(ref) if ref else None

def get_online_count():
    return 1

def set_user_online(email, session_id):
    pass

def heartbeat(email):
    pass