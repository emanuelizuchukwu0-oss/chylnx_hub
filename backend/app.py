# backend/app.py
import os
import requests
import uuid
import time
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room, leave_room

import psycopg2
import psycopg2.extras

# ---------------- App setup ----------------
app = Flask(__name__, template_folder="../frontend", static_folder="../static")
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")
app.config['SESSION_TYPE'] = 'filesystem'

socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_...")

# ---------------- PostgreSQL setup ----------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://chylnx_hub_user:Qz7ERTTXsstDh2cpjMPWMobvdj3oKORQ@dpg-d3br27b7mgec739v7hd0-a.oregon-postgres.render.com/chylnx_hub"
)

# Database connection with error handling
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

db = get_db_connection()
if db:
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
else:
    cursor = None
    print("⚠️  Running without database connection")

# ---------------- Database Initialization ----------------
def init_db():
    if not db:
        print("❌ No database connection available")
        return False
        
    try:
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
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                session_code VARCHAR(255) UNIQUE NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status VARCHAR(50) DEFAULT 'active'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id INT REFERENCES users(id) ON DELETE CASCADE,
                chat_session_id INT REFERENCES chat_sessions(id) ON DELETE SET NULL,
                reference VARCHAR(255),
                amount NUMERIC(10, 2),
                status VARCHAR(20) CHECK (status IN ('success', 'failed', 'pending')),
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

        db.commit()
        print("✅ Database tables initialized")
        return True
    except Exception as e:
        print(f"❌ Database init failed: {e}")
        import traceback
        traceback.print_exc()
        return False

# ---------------- Initialize Database ----------------
if db:
    init_db()
else:
    print("⚠️  Skipping database initialization - no connection")

# ---------------- Globals ----------------
chat_locked = False
next_game_time = datetime.utcnow() + timedelta(minutes=2, seconds=15)
game_active = False
connected_users = {}  # { sid: username }
current_chat_session = None

# ---------------- Helper Functions ----------------
def execute_query(query, params=None):
    """Safe database query execution with error handling"""
    if not db:
        return None
    try:
        cursor.execute(query, params or ())
        if query.strip().upper().startswith('SELECT'):
            return cursor.fetchall()
        else:
            db.commit()
            return True
    except Exception as e:
        print(f"❌ Query failed: {e}")
        db.rollback()
        return None

def get_user_id(username):
    """Safely get user ID"""
    result = execute_query("SELECT id FROM users WHERE username = %s", (username,))
    return result[0]['id'] if result else None

# ---------------- Routes ----------------
@app.route("/", methods=["GET"])
def index():
    try:
        username = session.get('username')
        paid = session.get('paid', False)
        
        # Only check database if we have a connection and username
        if db and username:
            result = execute_query("""
                SELECT p.id FROM payments p 
                JOIN users u ON p.user_id = u.id 
                WHERE u.username = %s AND p.status = 'success' 
                LIMIT 1
            """, (username,))
            paid = paid or (result is not None and len(result) > 0)
            
        return render_template("index.html", paid=paid)
    except Exception as e:
        print(f"❌ Error in index route: {e}")
        return render_template("index.html", paid=False)

@app.route("/health")
def health_check():
    """Health check endpoint for Render"""
    return jsonify({
        "status": "healthy", 
        "database": "connected" if db else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route("/set_timer", methods=["POST"])
def set_timer():
    global next_game_time
    try:
        hours = int(request.form.get("hours", 0))
        minutes = int(request.form.get("minutes", 2))
        seconds = int(request.form.get("seconds", 15))
        next_game_time = datetime.utcnow() + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        return redirect(url_for("index"))
    except Exception as e:
        print(f"❌ Error in set_timer: {e}")
        return redirect(url_for("index"))

@app.route("/admin_login", methods=["GET","POST"])
def admin_login():
    ADMIN_PASSCODE = os.getenv("ADMIN_PASSCODE", "12345")
    if request.method == "POST":
        if request.form.get("passcode") == ADMIN_PASSCODE:
            session["is_admin"] = True
            return redirect(url_for("admin"))
        return "<h3>❌ Wrong Passcode</h3><a href='/admin_login'>Try again</a>"
    return render_template("admin_login.html")

@app.route("/admin")
def admin():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    return render_template("admin.html")

@app.route("/payment")
def payment():
    return render_template("payment.html")

@app.route("/username", methods=['GET','POST'])
def username():
    if request.method == 'POST':
        new_name = request.form['username'].strip()
        if new_name:
            session['username'] = new_name
            if db:
                result = execute_query("SELECT id FROM users WHERE username = %s", (new_name,))
                if not result:
                    execute_query("INSERT INTO users (username) VALUES (%s) RETURNING id", (new_name,))
                    new_user = execute_query("SELECT id FROM users WHERE username = %s", (new_name,))
                    if new_user:
                        session['user_id'] = new_user[0]['id']
        return redirect(url_for('chat'))
    return render_template("username.html", username=session.get('username',''))

@app.route("/chat")
def chat():
    is_admin = session.get('is_admin', False)
    username = session.get('username')
    
    if not username:
        return redirect(url_for('username'))

    # Check payment status
    paid = session.get('paid', False)
    if db and not paid and not is_admin:
        result = execute_query("""
            SELECT p.id FROM payments p 
            JOIN users u ON p.user_id = u.id 
            WHERE u.username = %s AND p.status = 'success' 
            LIMIT 1
        """, (username,))
        paid = bool(result)

    if not paid and not is_admin:
        session['payment_required'] = True
        return render_template("chat.html", is_admin=is_admin, username=username, paid=False)

    if 'payment_required' in session:
        session.pop('payment_required', None)

    global chat_locked, next_game_time
    if chat_locked and not is_admin:
        if not next_game_time:
            next_game_time = datetime.utcnow() + timedelta(seconds=15)
        return render_template("locked.html", next_game_time=next_game_time)

    return render_template("chat.html", is_admin=is_admin, username=username, paid=True)

@app.route("/game")
def game():
    is_admin = session.get('is_admin', False)
    username = session.get('username')
    
    paid = session.get('paid', False)
    if db and not paid and not is_admin:
        result = execute_query("""
            SELECT p.id FROM payments p 
            JOIN users u ON p.user_id = u.id 
            WHERE u.username = %s AND p.status = 'success' 
            LIMIT 1
        """, (username,))
        paid = bool(result)

    if not paid and not is_admin:
        return render_template("payment_required.html")

    return render_template("game.html")

@app.route("/verify/<reference>")
def verify_payment(reference):
    username = session.get('username')
    if not username:
        return jsonify({"status":"failed","message":"User not logged in"}), 401

    if not db:
        return jsonify({"status":"error","message":"Database unavailable"}), 500

    result = execute_query("SELECT id, email FROM users WHERE username = %s", (username,))
    if not result:
        return jsonify({"status":"failed","message":"User not found"}), 404

    user_id = result[0]['id']
    email = result[0]['email']

    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

    success = result.get("status") and result.get("data", {}).get("status") == "success"
    amount = result.get("data", {}).get("amount", 0) / 100

    if success:
        session['paid'] = True
        session.modified = True
        
        execute_query("""
            INSERT INTO payments (user_id, reference, amount, status) 
            VALUES (%s, %s, %s, %s)
        """, (user_id, reference, amount, 'success'))
        
        return jsonify({"status":"success"})
    else:
        execute_query("""
            INSERT INTO payments (user_id, reference, amount, status) 
            VALUES (%s, %s, %s, %s)
        """, (user_id, reference, amount, 'failed'))
        return jsonify({"status":"failed","message":"Payment verification failed"})

# ---------------- SocketIO Events ----------------
@socketio.on("connect")
def handle_connect():
    username = session.get('username', f"Guest-{request.sid[:5]}")
    connected_users[request.sid] = username
    print(f"✅ {username} connected - Total users: {len(connected_users)}")

    if db:
        history = execute_query("""
            SELECT username AS from_user, message AS text, created_at AS timestamp
            FROM messages ORDER BY created_at DESC LIMIT 50
        """) or []
        emit("chat_history", history[::-1])
    
    emit("chat_status", {"locked": chat_locked})

@socketio.on("message")
def handle_message(data):
    username = connected_users.get(request.sid, "Unknown")
    if not username or username == "Unknown":
        emit("error", {"msg": "Please set a username first"})
        return

    if not db:
        emit("error", {"msg": "Database unavailable"})
        return

    user_result = execute_query("SELECT id FROM users WHERE username = %s", (username,))
    if not user_result:
        emit("error", {"msg": "User not found"})
        return

    user_id = user_result[0]['id']
    msg_text = data.get('text','').strip()
    if not msg_text:
        return

    # Insert message
    execute_query(
        "INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s)",
        (user_id, username, msg_text)
    )

    # Get the new message
    new_message = execute_query("""
        SELECT username, message, created_at 
        FROM messages 
        ORDER BY id DESC LIMIT 1
    """)
    
    if new_message:
        message_data = {
            "from": new_message[0]['username'],
            "text": new_message[0]['message'],
            "timestamp": new_message[0]['created_at'].isoformat()
        }
        emit("message", message_data, broadcast=True)

@socketio.on("disconnect")
def handle_disconnect():
    if request.sid in connected_users:
        user = connected_users.pop(request.sid)
        print(f"❌ {user} disconnected")

@socketio.on("lock_chat")
def lock_chat():
    if not session.get("is_admin"):
        emit("error", {"msg":"Only admin can lock chat"})
        return
    global chat_locked
    chat_locked = True
    emit("chat_status", {"locked": True}, broadcast=True)

@socketio.on("unlock_chat")
def unlock_chat():
    if not session.get("is_admin"):
        emit("error", {"msg":"Only admin can unlock chat"})
        return
    global chat_locked
    chat_locked = False
    emit("chat_status", {"locked": False}, broadcast=True)

@socketio.on("announce_winner")
def handle_announce_winner(data):
    if not session.get("is_admin"):
        emit("error", {"msg":"Only admin can announce winners"})
        return
        
    winners = data.get('winners', [])
    if not winners:
        emit("error", {"msg":"No winners selected"})
        return

    announcement = f"🏆 WINNER(S) ANNOUNCEMENT: {', '.join(winners)}! Congratulations! 🎉"
    emit("announcement", {"text": announcement}, broadcast=True)
    
    global chat_locked
    chat_locked = True
    emit("chat_status", {"locked": True}, broadcast=True)
    
    session.clear()
    emit("payment_reset", {}, broadcast=True)

# ---------------- Other Routes ----------------
@app.route("/register", methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        
        if not username or not email or not password:
            return "❌ All fields are required"

        if not db:
            return "❌ Database unavailable"

        result = execute_query("SELECT id FROM users WHERE email = %s", (email,))
        if result:
            return "❌ Email already registered"

        password_hash = generate_password_hash(password)
        execute_query("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)", 
                     (username, email, password_hash))
        
        user_result = execute_query("SELECT id FROM users WHERE email = %s", (email,))
        if user_result:
            session['user_id'] = user_result[0]['id']
            session['username'] = username
            return redirect(url_for('chat'))
            
    return render_template("register.html")

@app.route("/login", methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        
        if not db:
            return "❌ Database unavailable"

        result = execute_query("SELECT * FROM users WHERE email = %s", (email,))
        if result and check_password_hash(result[0]['password'], password):
            session['user_id'] = result[0]['id']
            session['username'] = result[0]['username']
            return redirect(url_for('chat'))
        else:
            return "❌ Invalid credentials"
            
    return render_template("login.html")

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "database": "connected" if db else "disconnected"})

# ---------------- Error Handlers ----------------
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    
    print(f"🚀 Starting server on port {port}")
    print(f"📊 Database status: {'Connected' if db else 'Disconnected'}")
    
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)