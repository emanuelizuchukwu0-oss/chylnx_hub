import os
import uuid
import time
import traceback
from datetime import datetime, timedelta
from flask import Flask, render_template, session, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import requests

# ---------------- Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend")
STATIC_DIR = os.path.join(TEMPLATE_DIR, "static")

# Debug logs for Render
print("🔎 BASE_DIR:", BASE_DIR)
print("🔎 TEMPLATE_DIR:", TEMPLATE_DIR)
print("🔎 STATIC_DIR:", STATIC_DIR)

try:
    if os.path.isdir(TEMPLATE_DIR):
        print("📂 Templates:", os.listdir(TEMPLATE_DIR)[:30])
    else:
        print("❌ Templates folder not found at", TEMPLATE_DIR)

    if os.path.isdir(STATIC_DIR):
        print("📂 Static:", os.listdir(STATIC_DIR)[:30])
    else:
        print("❌ Static folder not found at", STATIC_DIR)
except Exception as e:
    print("⚠ Error listing template/static:", e)

# ---------------- Flask ----------------
app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR
)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False

# ---------------- Socket.IO ----------------
REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL:
    print("🔑 Using Redis:", REDIS_URL)
    socketio = SocketIO(app, cors_allowed_origins="*", message_queue=REDIS_URL, manage_session=False)
else:
    socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# ---------------- Database ----------------
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ DB connection failed: {e}")
        return None

def execute_query(query, params=None, fetch=False):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(query, params or ())
            if fetch:
                result = cursor.fetchall()
            else:
                result = True
            conn.commit()
            return result
    except Exception as e:
        print(f"❌ Query failed: {e}")
        traceback.print_exc()
        conn.rollback()
        return None
    finally:
        conn.close()

# ---------------- Weekly Challenge Message Functions ----------------
def get_weekly_challenge_message():
    """Get the current weekly challenge message from database"""
    result = execute_query(
        "SELECT setting_value FROM app_settings WHERE setting_key = 'weekly_challenge_message'",
        fetch=True
    )
    if result:
        return result[0]['setting_value']
    return "WEEKLY CHALLENGE: COMPLETED"  # Default message

def set_weekly_challenge_message(message):
    """Set the weekly challenge message in database"""
    return execute_query(
        """INSERT INTO app_settings (setting_key, setting_value) 
           VALUES ('weekly_challenge_message', %s) 
           ON CONFLICT (setting_key) 
           DO UPDATE SET setting_value = %s""",
        (message, message)
    )

# ---------------- Initialize DB ----------------
def init_db():
    conn = get_db_connection()
    if not conn:
        print("❌ init_db: no DB connection")
        return
    try:
        with conn.cursor() as cursor:
            # Existing tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(255) UNIQUE NOT NULL,
                    email VARCHAR(255) UNIQUE,
                    password VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    username VARCHAR(255),
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    reference VARCHAR(255) UNIQUE NOT NULL,
                    amount NUMERIC(10,2) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS game_timer (
                    id SERIAL PRIMARY KEY,
                    end_time TIMESTAMP NOT NULL,
                    is_running BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_timer (
                    id SERIAL PRIMARY KEY,
                    end_time TIMESTAMP NOT NULL,
                    is_running BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS weekly_challenge (
                    id SERIAL PRIMARY KEY,
                    end_time TIMESTAMP,
                    is_active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # NEW: Add app_settings table for persistent messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    id SERIAL PRIMARY KEY,
                    setting_key VARCHAR(255) UNIQUE NOT NULL,
                    setting_value TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # WhatsApp-like chat enhancement tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id SERIAL PRIMARY KEY,
                    session_code VARCHAR(255) UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_status (
                    id SERIAL PRIMARY KEY,
                    message_id INT REFERENCES messages(id) ON DELETE CASCADE,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    status VARCHAR(50) DEFAULT 'sent',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(message_id, user_id)
                )
            """)
            
            # Create indexes for better performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at DESC)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_message_status_composite ON message_status(message_id, user_id)
            """)
            
            # Initialize with default message if not exists
            cursor.execute("""
                INSERT INTO app_settings (setting_key, setting_value) 
                VALUES ('weekly_challenge_message', 'WEEKLY CHALLENGE: COMPLETED')
                ON CONFLICT (setting_key) DO NOTHING
            """)

        conn.commit()
        print("✅ All tables initialized (including WhatsApp-like chat enhancements)")
    except Exception as e:
        print("❌ DB init failed:", e)
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
    finally:
        conn.close()

# Initialize database when app starts
init_db()

# ---------------- Timer Functions ----------------
def get_current_timer():
    result = execute_query(
        "SELECT * FROM game_timer WHERE is_running = TRUE ORDER BY created_at DESC LIMIT 1",
        fetch=True
    )
    if result:
        return result[0]
    return None

def set_timer(minutes, seconds):
    total_seconds = (minutes * 60) + seconds
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    execute_query("DELETE FROM game_timer")
    execute_query(
        "INSERT INTO game_timer (end_time, is_running) VALUES (%s, %s)",
        (end_time, True)
    )
    return end_time

def get_remaining_time():
    timer = get_current_timer()
    if not timer:
        return None
    
    end_time = timer['end_time']
    now = datetime.now()
    
    if end_time.tzinfo is not None and now.tzinfo is None:
        now = datetime.now(end_time.tzinfo)
    
    if now >= end_time:
        execute_query("UPDATE game_timer SET is_running = FALSE WHERE id = %s", (timer['id'],))
        return 0
    
    remaining = (end_time - now).total_seconds()
    return max(0, int(remaining))

# ---------------- Persistent Day Timer Functions ----------------
def get_current_day_timer():
    """Get the active day timer from database"""
    result = execute_query(
        "SELECT * FROM day_timer WHERE is_running = TRUE ORDER BY created_at DESC LIMIT 1",
        fetch=True
    )
    if result:
        return result[0]
    return None

def set_day_timer(days, hours, minutes, seconds):
    """Set a new day timer in the database - completely persistent"""
    total_seconds = (days * 24 * 60 * 60) + (hours * 60 * 60) + (minutes * 60) + seconds
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    
    # Clear ALL existing day timers first
    execute_query("DELETE FROM day_timer")
    
    # Create new day timer
    execute_query(
        "INSERT INTO day_timer (end_time, is_running) VALUES (%s, %s)",
        (end_time, True)
    )
    
    print(f"✅ Day timer set to expire at: {end_time}")
    return end_time

def get_day_remaining_time():
    """Calculate remaining time for active day timer - ALWAYS accurate"""
    timer = get_current_day_timer()
    if not timer:
        return None
    
    end_time = timer['end_time']
    now = datetime.now()
    
    # Handle timezone differences if necessary
    if end_time.tzinfo is not None and now.tzinfo is None:
        now = datetime.now(end_time.tzinfo)
    
    if now >= end_time:
        # Timer expired, update database
        execute_query("UPDATE day_timer SET is_running = FALSE WHERE id = %s", (timer['id'],))
        return 0
    
    remaining = (end_time - now).total_seconds()
    return max(0, int(remaining))

def check_day_timer_expired():
    """Check if day timer has expired and handle it - call this periodically"""
    timer = get_current_day_timer()
    if not timer:
        return False
    
    remaining = get_day_remaining_time()
    if remaining <= 0:
        # Timer expired, update database and broadcast
        execute_query("UPDATE day_timer SET is_running = FALSE WHERE id = %s", (timer['id'],))
        print("🎉 Day timer expired automatically")
        
        # Broadcast to all connected clients
        socketio.emit('day_timer_complete', {
            'message': 'DAY TIMER COMPLETED',
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        return True
    return False

def cleanup_old_messages():
    """Clean up messages older than 30 days (like WhatsApp)"""
    try:
        result = execute_query(
            "DELETE FROM messages WHERE created_at < NOW() - INTERVAL '30 days'"
        )
        if result:
            print("✅ Cleaned up old messages")
        return result
    except Exception as e:
        print(f"❌ Error cleaning up old messages: {e}")
        return None

# ---------------- Weekly Challenge Functions ----------------
def get_current_weekly_challenge():
    result = execute_query(
        "SELECT * FROM weekly_challenge WHERE is_active = TRUE ORDER BY created_at DESC LIMIT 1",
        fetch=True
    )
    if result:
        return result[0]
    return None

def set_weekly_challenge(days, action):
    if action == 'stop':
        execute_query("UPDATE weekly_challenge SET is_active = FALSE")
        return None
    
    total_seconds = days * 24 * 60 * 60
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    execute_query("UPDATE weekly_challenge SET is_active = FALSE")
    execute_query(
        "INSERT INTO weekly_challenge (end_time, is_active) VALUES (%s, %s)",
        (end_time, True)
    )
    return end_time

def get_weekly_remaining_time():
    challenge = get_current_weekly_challenge()
    if not challenge:
        return 0
    
    end_time = challenge['end_time']
    now = datetime.now()
    
    if end_time.tzinfo is not None and now.tzinfo is None:
        now = datetime.now(end_time.tzinfo)
    
    if now >= end_time:
        execute_query("UPDATE weekly_challenge SET is_active = FALSE WHERE id = %s", (challenge['id'],))
        return 0
    
    remaining = (end_time - now).total_seconds()
    return max(0, int(remaining))

# ---------------- App Logic ----------------
chat_locked = True
online_users = {}
user_activity = {}
connected_users = {}

@app.errorhandler(500)
def internal_error(e):
    print("❌ Internal Error:", e)
    traceback.print_exc()
    return "Internal server error", 500

# ---------------- Routes ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/payment")
def payment():
    return render_template("payment.html")

@app.route("/payment_required")
def payment_required():
    return render_template("payment_required.html")

@app.route("/set_username", methods=["GET", "POST"])
def set_username():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if not username:
            flash("Username required", "error")
            return redirect(url_for("set_username"))

        existing = execute_query("SELECT * FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if not existing:
            success = execute_query("INSERT INTO users (username) VALUES (%s)", (username,))
            if success:
                existing = execute_query("SELECT * FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
            else:
                flash("Error creating user account", "error")
                return redirect(url_for("set_username"))

        if existing:
            user = existing[0]
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session.modified = True
            flash("Username set!", "success")
            return redirect(url_for("chat"))

    return render_template("set_username.html")

@app.route("/chat")
def chat():
    return render_template("chat.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not email or not password:
            flash("All fields required", "error")
            return redirect(url_for("register"))

        existing = execute_query("SELECT id FROM users WHERE username=%s OR email=%s", (username, email), fetch=True)
        if existing:
            flash("Username/email exists", "error")
            return redirect(url_for("register"))

        execute_query("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
                      (username, email, generate_password_hash(password)))
        session["username"] = username
        flash("Registered!", "success")
        return redirect(url_for("chat"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        user = execute_query("SELECT * FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if user:
            user = user[0]
            if user.get("password") and check_password_hash(user["password"], password):
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                flash("Login success", "success")
                return redirect(url_for("chat"))
        flash("Invalid login", "error")
    return render_template("login.html")

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("passcode") == "12345":
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Wrong passcode", "error")
    return render_template("admin_login.html")

@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    users = execute_query("""
        SELECT 
            u.id, u.username, u.email, u.created_at,
            p.status AS last_payment_status,
            p.amount AS last_payment_amount,
            p.created_at AS last_payment_date
        FROM users u
        LEFT JOIN LATERAL (
            SELECT p2.* FROM payments p2 WHERE p2.user_id = u.id ORDER BY p2.created_at DESC LIMIT 1
        ) p ON TRUE
        ORDER BY u.created_at DESC
    """, fetch=True) or []

    return render_template("admin_dashboard.html", users=users, chat_locked=chat_locked)

@app.route("/toggle_chat_lock", methods=["POST"])
def toggle_chat_lock():
    global chat_locked
    chat_locked = not chat_locked
    return redirect(url_for("admin_dashboard"))

@app.route('/get_session_info')
def get_session_info():
    try:
        online_count = len(online_users)
        
        paid_users_result = execute_query(
            "SELECT COUNT(DISTINCT user_id) as count FROM payments WHERE status = 'success'",
            fetch=True
        )
        paid_users = paid_users_result[0]['count'] if paid_users_result else 0
        
        total_users_result = execute_query(
            "SELECT COUNT(*) as count FROM users",
            fetch=True
        )
        total_users = total_users_result[0]['count'] if total_users_result else 0
        
        session_code = f"SESSION_{int(time.time())}"
        
        return jsonify({
            'session_code': session_code,
            'paid_users': paid_users,
            'total_users': total_users,
            'online_users': online_count
        })
    except Exception as e:
        print(f"❌ Error getting session info: {e}")
        return jsonify({'error': 'Failed to get session info'}), 500

@app.route('/start_new_session', methods=['POST'])
def start_new_session():
    try:
        execute_query("UPDATE payments SET status = 'expired' WHERE status = 'success'")
        online_users.clear()
        user_activity.clear()
        
        socketio.emit('session_reset', {
            'message': 'Chat session has been reset. Payment required to continue.',
            'reset_by': session.get('username', 'Admin'),
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        
        return jsonify({
            'success': True,
            'message': 'Session reset successfully',
            'session_code': f"SESSION_{int(time.time())}"
        })
    except Exception as e:
        print(f"❌ Error resetting session: {e}")
        return jsonify({'error': 'Failed to reset session'}), 500

@app.route('/get_online_users')
def get_online_users():
    try:
        online_list = []
        current_time = time.time()
        
        inactive_users = []
        for username, last_active in user_activity.items():
            if current_time - last_active > 300:
                inactive_users.append(username)
        
        for username in inactive_users:
            user_activity.pop(username, None)
            online_users.pop(username, None)
        
        for username, user_info in online_users.items():
            online_list.append({
                'username': username,
                'user_id': user_info.get('user_id'),
                'connected_since': user_info.get('connected_at'),
                'last_activity': user_activity.get(username, 'Unknown')
            })
        
        return jsonify({'online_users': online_list})
    except Exception as e:
        print(f"❌ Error getting online users: {e}")
        return jsonify({'online_users': []})

@app.route('/get_payment_stats')
def get_payment_stats():
    try:
        today_payments = execute_query("""
            SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total 
            FROM payments 
            WHERE status = 'success' 
            AND DATE(created_at) = CURRENT_DATE
        """, fetch=True)
        
        total_payments = execute_query("""
            SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total 
            FROM payments 
            WHERE status = 'success'
        """, fetch=True)
        
        recent_payments = execute_query("""
            SELECT p.*, u.username 
            FROM payments p 
            JOIN users u ON p.user_id = u.id 
            WHERE p.status = 'success' 
            AND p.created_at >= NOW() - INTERVAL '24 hours'
            ORDER BY p.created_at DESC
            LIMIT 50
        """, fetch=True) or []
        
        return jsonify({
            'today': {
                'count': today_payments[0]['count'] if today_payments else 0,
                'total_amount': float(today_payments[0]['total']) if today_payments else 0
            },
            'total': {
                'count': total_payments[0]['count'] if total_payments else 0,
                'total_amount': float(total_payments[0]['total']) if total_payments else 0
            },
            'recent_payments': recent_payments
        })
    except Exception as e:
        print(f"❌ Error getting payment stats: {e}")
        return jsonify({'error': 'Failed to get payment stats'}), 500

# ---------------- Socket.IO Event Handlers ----------------
def check_and_broadcast_timer_status():
    """Check timer status and broadcast if needed"""
    timer = get_current_timer()
    if not timer:
        socketio.emit('game_started', {
            'message': 'GAME STARTED',
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        return True
    return False

@socketio.on("connect")
def handle_connect():
    """Send current timer states when client connects"""
    print("🔔 Client connected")
    
    # Check game timer status
    if check_and_broadcast_timer_status():
        return
    
    # Send current game timer state
    remaining = get_remaining_time()
    if remaining is not None and remaining > 0:
        emit('timer_update', {
            'remaining_seconds': remaining,
            'is_running': True
        })
    else:
        emit('timer_update', {
            'remaining_seconds': 0,
            'is_running': False
        })
    
    # ALWAYS send the current ACTUAL day timer state from database
    handle_get_day_timer()

@socketio.on("set_timer")
def handle_set_timer(data):
    try:
        minutes = int(data.get('minutes', 5))
        seconds = int(data.get('seconds', 0))
        
        if minutes == 0 and seconds == 0:
            emit('timer_error', {'message': 'Please set a valid timer duration'})
            return
        
        print(f"⏰ Setting timer: {minutes}m {seconds}s")
        end_time = set_timer(minutes, seconds)
        remaining = get_remaining_time()
        
        print(f"✅ Timer set. End time: {end_time}, Remaining: {remaining}s")
        
        emit('timer_update', {
            'remaining_seconds': remaining,
            'is_running': True
        }, broadcast=True)
        
    except Exception as e:
        print(f"❌ Timer setting error: {e}")
        emit('timer_error', {'message': str(e)})

@socketio.on("get_timer")
def handle_get_timer():
    remaining = get_remaining_time()
    print(f"📡 Sending timer state: {remaining}s")
    
    if remaining is not None and remaining > 0:
        emit('timer_update', {
            'remaining_seconds': remaining,
            'is_running': True
        })
    else:
        emit('timer_update', {
            'remaining_seconds': 0,
            'is_running': False
        })

@socketio.on("set_day_timer")
def handle_set_day_timer(data):
    """Handle day timer setting from admin - completely persistent"""
    try:
        days = int(data.get('days', 0))
        hours = int(data.get('hours', 0))
        minutes = int(data.get('minutes', 0))
        seconds = int(data.get('seconds', 0))
        
        total_seconds = (days * 24 * 60 * 60) + (hours * 60 * 60) + (minutes * 60) + seconds
        
        if total_seconds <= 0:
            emit('day_timer_error', {'message': 'Please set a valid timer duration'})
            return
        
        print(f"📅 Setting persistent day timer: {days}d {hours}h {minutes}m {seconds}s")
        end_time = set_day_timer(days, hours, minutes, seconds)
        remaining = get_day_remaining_time()
        
        print(f"✅ Persistent day timer set. Will expire at: {end_time}")
        
        # Broadcast to all connected clients
        emit('day_timer_update', {
            'remaining_seconds': remaining,
            'is_running': True,
            'message': f'Day timer set for {days}d {hours}h {minutes}m {seconds}s'
        }, broadcast=True)
        
    except Exception as e:
        print(f"❌ Day timer setting error: {e}")
        emit('day_timer_error', {'message': str(e)})

@socketio.on("get_day_timer")
def handle_get_day_timer():
    """Send current ACTUAL day timer state from database"""
    # First check if timer expired
    check_day_timer_expired()
    
    timer = get_current_day_timer()
    if timer:
        remaining = get_day_remaining_time()
        if remaining > 0:
            # Convert to readable format
            days = remaining // (24 * 3600)
            hours = (remaining % (24 * 3600)) // 3600
            minutes = (remaining % 3600) // 60
            seconds = remaining % 60
            
            emit('day_timer_update', {
                'remaining_seconds': remaining,
                'is_running': True,
                'readable_time': f'{days}d {hours}h {minutes}m {seconds}s',
                'message': f'Day timer active: {days}d {hours}h {minutes}m {seconds}s remaining'
            })
        else:
            emit('day_timer_update', {
                'remaining_seconds': 0,
                'is_running': False,
                'message': 'Day timer completed'
            })
    else:
        emit('day_timer_update', {
            'remaining_seconds': 0,
            'is_running': False,
            'message': 'No active day timer'
        })

@socketio.on("stop_day_timer")
def handle_stop_day_timer():
    """Stop the current day timer"""
    try:
        timer = get_current_day_timer()
        if timer:
            execute_query("UPDATE day_timer SET is_running = FALSE WHERE id = %s", (timer['id'],))
            print("⏹️ Day timer stopped manually")
        
        emit('day_timer_update', {
            'remaining_seconds': 0,
            'is_running': False,
            'message': 'Day timer stopped'
        }, broadcast=True)
        
    except Exception as e:
        print(f"❌ Error stopping day timer: {e}")
        emit('day_timer_error', {'message': str(e)})

@socketio.on("set_weekly_challenge")
def handle_set_weekly_challenge(data):
    try:
        days = int(data.get('days', 7))
        action = data.get('action', 'start')
        
        if action == 'start' and days <= 0:
            emit('weekly_challenge_error', {'message': 'Please set a valid duration for weekly challenge'})
            return
        
        print(f"📅 Setting weekly challenge: {action}, {days} days")
        
        if action == 'start':
            end_time = set_weekly_challenge(days, action)
            remaining = get_weekly_remaining_time()
            is_active = True
        else:
            set_weekly_challenge(days, action)
            remaining = 0
            is_active = False
        
        print(f"✅ Weekly challenge {action}. Remaining: {remaining}s")
        
        emit('weekly_challenge_update', {
            'remaining_seconds': remaining,
            'is_active': is_active
        }, broadcast=True)
        
    except Exception as e:
        print(f"❌ Weekly challenge setting error: {e}")
        emit('weekly_challenge_error', {'message': str(e)})

@socketio.on("get_weekly_challenge")
def handle_get_weekly_challenge():
    challenge = get_current_weekly_challenge()
    if challenge:
        remaining = get_weekly_remaining_time()
        emit('weekly_challenge_update', {
            'remaining_seconds': remaining,
            'is_active': True
        })
    else:
        emit('weekly_challenge_update', {
            'remaining_seconds': 0,
            'is_active': False
        })

@socketio.on("join_chat")
def handle_join(data):
    username = data.get("username")
    if not username:
        return

    user = execute_query("SELECT id FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
    if user:
        user_id = user[0]["id"]
        online_users[username] = {
            'user_id': user_id,
            'connected_at': datetime.utcnow().isoformat(),
            'sid': request.sid
        }
        user_activity[username] = time.time()

    connected_users[username] = request.sid
    print("✅ {} joined, total users: {}".format(username, len(connected_users)))

    # Enhanced chat history with WhatsApp-like features
    history = execute_query("""
        SELECT 
            m.id,
            u.username AS "from", 
            m.message AS text, 
            m.created_at AS timestamp,
            COALESCE(ms.status, 'delivered') AS status
        FROM messages m
        JOIN users u ON m.user_id = u.id
        LEFT JOIN message_status ms ON m.id = ms.message_id AND ms.user_id = %s
        WHERE m.created_at >= NOW() - INTERVAL '7 days'  -- Last 7 days only
        ORDER BY m.created_at DESC
        LIMIT 200  -- Recent messages first, limit for performance
    """, (user[0]["id"] if user else None,), fetch=True) or []

    # Convert to WhatsApp-like format and reverse for chronological order
    formatted_history = []
    for h in history:
        if h.get("timestamp") is not None and isinstance(h["timestamp"], datetime):
            h["timestamp"] = h["timestamp"].isoformat()
        
        # Add message ID and status for frontend tracking
        formatted_history.append({
            "id": h["id"],
            "from": h["from"],
            "text": h["text"],
            "timestamp": h["timestamp"],
            "status": h["status"]
        })

    # Reverse to show oldest first (like WhatsApp)
    formatted_history.reverse()

    emit("chat_history", formatted_history, to=request.sid)
    
    # Update message status to 'read' for all messages this user is seeing
    if user:
        execute_query("""
            UPDATE message_status 
            SET status = 'read' 
            WHERE user_id = %s AND status = 'delivered'
        """, (user[0]["id"],))
    
    socketio.emit("user_count_update", {"count": len(connected_users)})

@socketio.on("message")
def handle_message(data):
    try:
        print("📨 Message received (raw):", data)

        username = None
        for u, sid in connected_users.items():
            if sid == request.sid:
                username = u
                break

        if not username:
            username = data.get("from")
        if not username:
            print("⚠ Could not determine message sender for sid:", request.sid)
            return

        text = (data.get("text") or "").strip()
        if not text:
            return

        if username in user_activity:
            user_activity[username] = time.time()

        user = execute_query("SELECT id FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if not user:
            return

        user_id = user[0]["id"]
        
        # Insert message with current session context
        message_id = execute_query(
            "INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s) RETURNING id",
            (user_id, username, text),
            fetch=True
        )
        
        if message_id:
            message_id = message_id[0]['id']
            
            # Mark message as sent for the sender immediately
            execute_query(
                "INSERT INTO message_status (message_id, user_id, status) VALUES (%s, %s, %s)",
                (message_id, user_id, 'sent')
            )
            
            # Get current session code
            session_code = f"SESSION_{int(time.time()) // 3600}"  # Hourly sessions
            
            msg = {
                "id": message_id,  # Add message ID for status tracking
                "from": username,
                "text": text,
                "timestamp": datetime.utcnow().isoformat(),
                "status": "sent",  # Initial status
                "session": session_code
            }

            # Broadcast to all connected users
            emit("message", msg, broadcast=True)
            
            # Update status to 'delivered' for all online users
            for online_username, user_info in online_users.items():
                if online_username != username:  # Don't update sender's status
                    online_user_id = user_info.get('user_id')
                    if online_user_id:
                        execute_query(
                            "INSERT INTO message_status (message_id, user_id, status) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (message_id, online_user_id, 'delivered')
                        )
            
            print(f"✅ Message saved and broadcast: {text[:50]}...")

    except Exception as e:
        print("❌ handle_message error:", e)
        traceback.print_exc()

@socketio.on("disconnect")
def handle_disconnect():
    username_to_remove = None
    for username, sid in list(connected_users.items()):
        if sid == request.sid:
            username_to_remove = username
            break

    if username_to_remove:
        del connected_users[username_to_remove]
        online_users.pop(username_to_remove, None)
        user_activity.pop(username_to_remove, None)
        print("❌ {} left, total users: {}".format(username_to_remove, len(connected_users)))

    socketio.emit("user_count_update", {"count": len(connected_users)})

@socketio.on("announce_winner")
def handle_announce_winner(data):
    try:
        winners = data.get("winners")
        if not winners:
            return
            
        print(f"🎉 Broadcasting winner announcement: {winners}")
        
        emit(
            "winner_announced", 
            {"winners": winners}, 
            broadcast=True,
            include_self=True
        )
        
        print("✅ Winner announcement broadcasted to all users")
        
    except Exception as e:
        print(f"❌ Error in handle_announce_winner: {e}")
        traceback.print_exc()

@socketio.on("timer_finished")
def handle_timer_finished():
    try:
        print("🎉 Timer finished - broadcasting GAME STARTED")
        
        execute_query("UPDATE game_timer SET is_running = FALSE WHERE is_running = TRUE")
        
        emit('game_started', {
            'message': 'GAME STARTED',
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        
        print("✅ Game started announcement broadcasted to all users")
        
    except Exception as e:
        print(f"❌ Error in handle_timer_finished: {e}")
        traceback.print_exc()

@socketio.on("day_timer_finished")
def handle_day_timer_finished():
    """Handle day timer completion - only called when timer actually expires"""
    try:
        # Double check it's actually expired
        if check_day_timer_expired():
            print("🎉 Day timer finished - broadcasting completion")
            
            # Broadcast to ALL connected clients
            emit('day_timer_complete', {
                'message': 'DAY TIMER COMPLETED',
                'timestamp': datetime.utcnow().isoformat()
            }, broadcast=True)
            
            print("✅ Day timer completion broadcasted to all users")
        
    except Exception as e:
        print(f"❌ Error in handle_day_timer_finished: {e}")
        traceback.print_exc()

@socketio.on("weekly_challenge_finished")
def handle_weekly_challenge_finished():
    try:
        print("🎉 Weekly challenge finished - broadcasting completion")
        
        execute_query("UPDATE weekly_challenge SET is_active = FALSE WHERE is_active = TRUE")
        
        emit('weekly_challenge_complete', {
            'message': 'WEEKLY CHALLENGE: COMING SOON',
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        
        print("✅ Weekly challenge completion broadcasted to all users")
        
    except Exception as e:
        print(f"❌ Error in handle_weekly_challenge_finished: {e}")
        traceback.print_exc()

# ---------------- WhatsApp-like Chat Functions ----------------
@socketio.on("message_delivered")
def handle_message_delivered(data):
    """Mark message as delivered for a specific user"""
    try:
        message_id = data.get('message_id')
        username = data.get('username')
        
        user = execute_query("SELECT id FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if user and message_id:
            execute_query(
                "INSERT INTO message_status (message_id, user_id, status) VALUES (%s, %s, %s) ON CONFLICT (message_id, user_id) DO UPDATE SET status = 'delivered'",
                (message_id, user[0]["id"], 'delivered')
            )
            
            # Notify sender that message was delivered
            emit("message_status_update", {
                'message_id': message_id,
                'status': 'delivered',
                'to_user': username
            }, broadcast=True)
            
    except Exception as e:
        print(f"❌ Error updating message delivery: {e}")

@socketio.on("message_read")
def handle_message_read(data):
    """Mark message as read by a user"""
    try:
        message_id = data.get('message_id')
        username = data.get('username')
        
        user = execute_query("SELECT id FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if user and message_id:
            execute_query(
                "UPDATE message_status SET status = 'read' WHERE message_id = %s AND user_id = %s",
                (message_id, user[0]["id"])
            )
            
            # Notify sender that message was read
            emit("message_status_update", {
                'message_id': message_id,
                'status': 'read',
                'to_user': username
            }, broadcast=True)
            
    except Exception as e:
        print(f"❌ Error updating message read status: {e}")

@socketio.on("typing_start")
def handle_typing_start(data):
    """Handle typing indicator"""
    username = data.get('username')
    if username:
        emit("user_typing", {
            'username': username,
            'is_typing': True
        }, broadcast=True, include_self=False)

@socketio.on("typing_stop")
def handle_typing_stop(data):
    """Handle typing stop indicator"""
    username = data.get('username')
    if username:
        emit("user_typing", {
            'username': username,
            'is_typing': False
        }, broadcast=True, include_self=False)

# ---------------- Payment Routes ----------------
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_...")

@app.route("/initialize_payment", methods=["POST"])
def initialize_payment():
    try:
        user_id = session.get("user_id")
        username = session.get("username")

        if not user_id or not username:
            return jsonify({"error": "User not logged in"}), 401

        reference = str(uuid.uuid4())
        amount_naira = 500
        amount_kobo = amount_naira * 100

        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "email": f"{username}@chylnx.com",
            "amount": amount_kobo,
            "reference": reference,
            "callback_url": f"{request.host_url}payment_verify",
            "metadata": {
                "user_id": user_id,
                "username": username
            }
        }

        response = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers=headers,
            json=data
        )

        if response.status_code == 200:
            result = response.json()
            session['payment_reference'] = reference
            return jsonify({
                "authorization_url": result['data']['authorization_url'],
                "reference": reference
            })
        else:
            print("❌ Paystack init error:", response.text)
            return jsonify({"error": "Payment initialization failed"}), 400

    except Exception as e:
        print(f"❌ Payment initialization error: {e}")
        return jsonify({"error": "Payment initialization failed"}), 500

@app.route("/payment_verify")
def payment_verify():
    try:
        reference = request.args.get('reference') or request.args.get('trxref')
        payment_ref = reference or session.get('payment_reference')
        
        if not payment_ref:
            print("❌ No payment reference found")
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"status": "error", "message": "No reference found"}), 400
            else:
                flash("Payment verification failed: No reference found", "error")
                return redirect(url_for("payment"))

        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{payment_ref}",
            headers=headers
        )

        if response.status_code == 200:
            result = response.json()
            if result.get('data') and result['data']['status'] == 'success':
                user_id = session.get("user_id")
                amount = result['data']['amount'] / 100

                success = execute_query(
                    "INSERT INTO payments (user_id, reference, amount, status) VALUES (%s, %s, %s, %s)",
                    (user_id, payment_ref, amount, 'success')
                )

                if success:
                    session['paid'] = True
                    print("✅ Payment successful:", payment_ref)

                    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return jsonify({"status": "success", "reference": payment_ref})
                    else:
                        flash("Payment successful! You can now access the chat.", "success")
                        return redirect(url_for("index"))

                else:
                    if request.is_json:
                        return jsonify({"status": "error", "message": "Payment record failed"}), 500
                    else:
                        flash("Payment recorded failed. Please contact support.", "error")
                        return redirect(url_for("payment"))
            else:
                if request.is_json:
                    return jsonify({"status": "failed", "message": "Verification failed"}), 400
                else:
                    flash("Payment verification failed. Please try again.", "error")
                    return redirect(url_for("payment"))
        else:
            print("❌ Paystack verify error:", response.text)
            if request.is_json:
                return jsonify({"status": "error", "message": "Paystack API error"}), 500
            else:
                flash("Payment verification failed. Please try again.", "error")
                return redirect(url_for("payment"))

    except Exception as e:
        print(f"❌ Payment verification error: {e}")
        if request.is_json:
            return jsonify({"status": "error", "message": "Exception occurred"}), 500
        else:
            flash("Payment verification failed. Please try again.", "error")
            return redirect(url_for("payment"))

@app.route("/check_payment_status")
def check_payment_status():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"paid": False})

    payment = execute_query(
        "SELECT * FROM payments WHERE user_id = %s AND status = 'success' LIMIT 1",
        (user_id,), fetch=True
    )

    if payment:
        session['paid'] = True
        return jsonify({"paid": True})
    else:
        return jsonify({"paid": False})

# Update your manual_weekly_complete handler to save to database
@socketio.on("manual_weekly_complete")
def handle_manual_weekly_complete(data):
    """Manually trigger weekly challenge completion with custom message - PERSISTENT"""
    try:
        custom_message = data.get('message', 'WEEKLY CHALLENGE: COMPLETED')
        
        print(f"🎉 Manually triggering weekly challenge completion: {custom_message}")
        
        # Save message to database for persistence
        set_weekly_challenge_message(custom_message)
        
        # Update database to mark as inactive
        execute_query("UPDATE weekly_challenge SET is_active = FALSE WHERE is_active = TRUE")
        
        # Broadcast with custom message
        emit('weekly_challenge_complete', {
            'message': custom_message,
            'timestamp': datetime.utcnow().isoformat(),
            'manual_trigger': True,
            'persistent': True
        }, broadcast=True)
        
        print(f"✅ Manual weekly challenge completion broadcasted and saved: {custom_message}")
        
    except Exception as e:
        print(f"❌ Error in manual weekly completion: {e}")
        traceback.print_exc()
        emit('weekly_challenge_error', {'message': str(e)})

# Add this new route to get the current message
@app.route('/get_weekly_message')
def get_weekly_message():
    """API endpoint to get current weekly challenge message"""
    try:
        message = get_weekly_challenge_message()
        return jsonify({
            'message': message,
            'success': True
        })
    except Exception as e:
        print(f"❌ Error getting weekly message: {e}")
        return jsonify({'error': 'Failed to get message'}), 500

# Add socket event to get message
@socketio.on("get_weekly_message")
def handle_get_weekly_message():
    """Send current weekly challenge message to client"""
    try:
        message = get_weekly_challenge_message()
        emit('weekly_message_update', {
            'message': message
        })
    except Exception as e:
        print(f"❌ Error in get_weekly_message: {e}")
        emit('weekly_challenge_error', {'message': str(e)})

@socketio.on("set_weekly_message")
def handle_set_weekly_message(data):
    """Save weekly challenge message to database"""
    try:
        message = data.get('message', 'WEEKLY CHALLENGE: COMPLETED')
        
        # Save to database
        success = set_weekly_challenge_message(message)
        
        if success:
            print(f"💾 Weekly message saved to database: {message}")
            emit('set_weekly_message_response', {
                'success': True,
                'message': message
            })
            
            # Also update the current display for all users
            emit('weekly_message_update', {
                'message': message
            }, broadcast=True)
        else:
            emit('set_weekly_message_response', {
                'success': False,
                'error': 'Failed to save message to database'
            })
            
    except Exception as e:
        print(f"❌ Error setting weekly message: {e}")
        emit('set_weekly_message_response', {
            'success': False,
            'error': str(e)
        })

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)