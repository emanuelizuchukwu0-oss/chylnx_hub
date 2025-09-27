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
# serve HTML from frontend/ and static files from static/
app = Flask(__name__, template_folder="frontend", static_folder="static")
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")

socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_...")

# ---------------- PostgreSQL setup ----------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://chylnx_hub_user:Qz7ERTTXsstDh2cpjMPWMobvdj3oKORQ@dpg-d3br27b7mgec739v7hd0-a/chylnx_hub"
)

# connect and use RealDictCursor to get dict-like rows
db = psycopg2.connect(DATABASE_URL)
cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ---------------- Database Initialization ----------------
def init_db():
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
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id SERIAL PRIMARY KEY,
            session_code VARCHAR(255) UNIQUE NOT NULL,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            status VARCHAR(50) DEFAULT 'active'
        )
    """)
    db.commit()
    print("✅ Database tables initialized")

# call once at startup
init_db()

# ---------------- Globals ----------------
chat_locked = False
next_game_time = datetime.utcnow() + timedelta(minutes=2, seconds=15)
game_active = False
connected_users = {}  # { sid: username }
current_chat_session = None

# ---------------- Routes ----------------
@app.route("/", methods=["GET"])
def index():
    try:
        username = session.get('username')
        paid_db = False
        if username:
            cursor.execute("""
                SELECT p.id
                FROM payments p
                JOIN users u ON p.user_id = u.id
                WHERE u.username=%s AND p.status='success'
                ORDER BY p.created_at DESC
                LIMIT 1
            """, (username,))
            paid_db = cursor.fetchone() is not None
        paid = session.get('paid', False) or paid_db
        return render_template("index.html", paid=paid)
    except Exception as e:
        import traceback
        print("Error in index route:", e)
        traceback.print_exc()
        return "Internal Server Error", 500


@app.route("/set_timer", methods=["POST"])
def set_timer():
    global next_game_time
    hours = int(request.form.get("hours", 0))
    minutes = int(request.form.get("minutes", 2))
    seconds = int(request.form.get("seconds", 15))
    next_game_time = datetime.utcnow() + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return redirect(url_for("index"))

@app.route("/admin_login", methods=["GET","POST"])
def admin_login():
    ADMIN_PASSCODE = os.getenv("ADMIN_PASSCODE","12345")
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
            cursor.execute("SELECT id FROM users WHERE username=%s", (new_name,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO users (username) VALUES (%s) RETURNING id", (new_name,))
                new_user = cursor.fetchone()
                db.commit()
                session['user_id'] = new_user['id']
        return redirect(url_for('chat'))
    return render_template("username.html", username=session.get('username',''))

def has_paid():
    user_id = session.get('user_id')
    if not user_id:
        return False
    cursor.execute("SELECT COUNT(*) AS count FROM payments WHERE user_id=%s AND status='success'", (user_id,))
    result = cursor.fetchone()
    return result['count'] > 0

@app.route("/chat")
def chat():
    is_admin = session.get('is_admin', False)
    username = session.get('username')
    if not username:
        return redirect(url_for('username'))

    cursor.execute("""
        SELECT p.id 
        FROM payments p
        JOIN users u ON p.user_id = u.id
        WHERE u.username=%s AND p.status='success'
        ORDER BY p.created_at DESC
        LIMIT 1
    """, (username,))
    paid_db = cursor.fetchone()
    paid = session.get('paid', False) or bool(paid_db)

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
    cursor.execute("""
        SELECT p.id 
        FROM payments p
        JOIN users u ON p.user_id = u.id
        WHERE u.username=%s AND p.status='success'
        ORDER BY p.created_at DESC
        LIMIT 1
    """, (username,))
    paid_db = cursor.fetchone()
    paid = session.get('paid', False) or bool(paid_db)
    if not paid and not is_admin:
        return render_template("payment_required.html")
    return render_template("game.html")

@app.route("/verify/<reference>")
def verify_payment(reference):
    username = session.get('username')
    if not username:
        return jsonify({"status":"failed","message":"User not logged in"}), 401

    cursor.execute("SELECT id, email FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    if not user:
        return jsonify({"status":"failed","message":"User not found"}), 404

    user_id = user['id']
    email = user['email']

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
        cursor.execute("""
            INSERT INTO payments (user_id, reference, amount, status)
            VALUES (%s, %s, %s, %s)
        """, (user_id, reference, amount, 'success'))
        db.commit()
        return jsonify({"status":"success"})
    else:
        cursor.execute("""
            INSERT INTO payments (user_id, reference, amount, status)
            VALUES (%s, %s, %s, %s)
        """, (user_id, reference, amount, 'failed'))
        db.commit()
        return jsonify({"status":"failed","message":"Payment verification failed"})

# ---------------- SocketIO Events ----------------
@socketio.on("connect")
def handle_connect():
    username = session.get('username', f"Guest-{request.sid[:5]}")
    connected_users[request.sid] = username
    print(f"✅ {username} connected ({request.sid}) - Total users: {len(connected_users)}")

    cursor.execute("""
        SELECT m.username AS from_user, m.message AS text, m.created_at AS timestamp
        FROM messages m
        ORDER BY m.created_at DESC
        LIMIT 50
    """)
    history = cursor.fetchall()
    emit("chat_history", history[::-1])
    emit("chat_status", {"locked": chat_locked})

@socketio.on("message")
def handle_message(data):
    username = connected_users.get(request.sid, "Unknown")
    if not username or username == "Unknown":
        emit("error", {"msg": "Please set a username first"})
        return

    cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    if not user:
        emit("error", {"msg": "User not found"})
        return

    user_id = user['id']
    msg_text = data.get('text','').strip()
    if not msg_text:
        return

    cursor.execute(
        "INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s) RETURNING id, username, message, created_at",
        (user_id, username, msg_text)
    )
    new_message = cursor.fetchone()
    db.commit()

    message_data = {
        "from": new_message['username'],
        "text": new_message['message'],
        "timestamp": new_message['created_at'].isoformat()
    }

    print(f"📢 Broadcasting message from {username}: {msg_text}")
    emit("message", message_data, broadcast=True)

@socketio.on("disconnect")
def handle_disconnect():
    if request.sid in connected_users:
        user = connected_users.pop(request.sid)
        print(f"{user} disconnected")

@socketio.on("lock_chat")
def lock_chat():
    if not session.get("is_admin"):
        emit("error", {"msg":"Only admin can lock chat"}, room=request.sid); return
    global chat_locked; chat_locked = True
    emit("chat_status", {"locked": True}, broadcast=True)

@socketio.on("unlock_chat")
def unlock_chat():
    if not session.get("is_admin"):
        emit("error", {"msg":"Only admin can unlock chat"}, room=request.sid); return
    global chat_locked; chat_locked = False
    emit("chat_status", {"locked": False}, broadcast=True)

@socketio.on("announce_winner")
def handle_announce_winner(data):
    if not session.get("is_admin"):
        emit("error", {"msg":"Only admin can announce winners"}, room=request.sid); return
    winners = data.get('winners', [])
    if not winners:
        emit("error", {"msg":"No winners selected"}, room=request.sid); return

    announcement = f"🏆 WINNER(S) ANNOUNCEMENT: {', '.join(winners)}! Congratulations! 🎉"
    emit("announcement", {"text": announcement}, broadcast=True)
    global chat_locked; chat_locked = True
    emit("chat_status", {"locked": True}, broadcast=True)
    session.clear()
    emit("payment_reset", {}, broadcast=True)

# ---------------- Register/Login ----------------
@app.route("/register", methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        if not username or not email or not password:
            return "❌ All fields are required"

        cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cursor.fetchone():
            return "❌ Email already registered"

        password_hash = generate_password_hash(password)
        cursor.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s) RETURNING id", 
                       (username, email, password_hash))
        new_user = cursor.fetchone()
        db.commit()
        session['user_id'] = new_user['id']
        session['username'] = username
        return redirect(url_for('chat'))
    return render_template("register.html")

@app.route("/login", methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('chat'))
        else:
            return "❌ Invalid credentials"
    return render_template("login.html")

@app.route("/admin_dashboard")
def admin_dashboard():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    cursor.execute("""
        SELECT u.id, u.username, u.email, 
               p.status AS last_payment_status, 
               p.amount AS last_payment_amount,
               p.created_at AS last_payment_date
        FROM users u
        LEFT JOIN payments p ON p.user_id = u.id
        AND p.created_at = (
            SELECT MAX(created_at) 
            FROM payments 
            WHERE user_id = u.id
        )
        ORDER BY u.id DESC
    """)
    users = cursor.fetchall()
    global chat_locked
    return render_template("admin_dashboard.html", users=users, chat_locked=chat_locked)

@app.route("/toggle_chat_lock", methods=["POST"])
def toggle_chat_lock():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    global chat_locked
    chat_locked = not chat_locked
    return redirect(url_for("admin_dashboard"))

@app.route("/check_payment_status")
def check_payment_status():
    username = session.get('username')
    if not username:
        return jsonify({"error":"Not logged in"}), 401
    cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    if not user:
        return jsonify({"error":"User not found"}), 404
    cursor.execute("SELECT COUNT(*) AS count FROM payments WHERE user_id=%s AND status='success'", (user['id'],))
    result = cursor.fetchone()
    return jsonify({
        "username": username,
        "paid_in_session": session.get('paid', False),
        "paid_in_db": result['count'] > 0
    })

@app.route("/test_payment")
def test_payment():
    if not session.get('username'):
        return "Please log in first"
    session['paid'] = True
    session.modified = True
    cursor.execute("SELECT id FROM users WHERE username=%s", (session.get('username'),))
    user = cursor.fetchone()
    if user:
        cursor.execute("INSERT INTO payments (user_id, reference, amount, status) VALUES (%s, %s, %s, %s)", (user['id'], 'test_ref', 0, 'success'))
        db.commit()
    return "OK"

@app.route("/start_new_session", methods=["POST"])
def start_new_session():
    if not session.get("is_admin"):
        return jsonify({"error":"Admin access required"}), 403
    global current_chat_session
    try:
        if current_chat_session:
            cursor.execute("UPDATE chat_sessions SET end_time = CURRENT_TIMESTAMP, status='completed' WHERE id=%s", (current_chat_session,))
        session_code = f"CHAT_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        cursor.execute("INSERT INTO chat_sessions (session_code, status) VALUES (%s, 'active') RETURNING id", (session_code,))
        row = cursor.fetchone()
        db.commit()
        current_chat_session = row['id']
        session.clear()
        socketio.emit("session_reset", {"message":"Chat session reset! Payment required to continue chatting.", "session_code": session_code, "timestamp": datetime.utcnow().isoformat()}, broadcast=True)
        print(f"🔄 Chat session reset by admin. New session: {session_code}")
        return jsonify({"success": True, "session_code": session_code, "message":"Chat session reset successfully"})
    except Exception as e:
        print("Error resetting session:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/get_session_info")
def get_session_info():
    try:
        cursor.execute("""
            SELECT cs.session_code, cs.start_time, 
                   COUNT(DISTINCT p.user_id) as paid_users,
                   (SELECT COUNT(*) FROM users) as total_users
            FROM chat_sessions cs
            LEFT JOIN payments p ON p.chat_session_id = cs.id AND p.status = 'success'
            WHERE cs.status = 'active'
            GROUP BY cs.id, cs.session_code, cs.start_time
            ORDER BY cs.id DESC LIMIT 1
        """)
        session_info = cursor.fetchone()
        if session_info:
            return jsonify({
                "session_code": session_info['session_code'],
                "start_time": session_info['start_time'].isoformat() if hasattr(session_info['start_time'],'isoformat') else session_info['start_time'],
                "paid_users": session_info['paid_users'],
                "total_users": session_info['total_users']
            })
        return jsonify({"error":"No active session found"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------- Run ----------------
if __name__ == "__main__":
    # In production, Render uses gunicorn; this is for local testing
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
