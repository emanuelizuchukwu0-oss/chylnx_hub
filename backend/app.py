import os
import uuid
from datetime import datetime
from flask import Flask, render_template, session, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras

# ---------------- Absolute Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # chylnx_backend
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend", "chylnx_hub")
STATIC_DIR = os.path.join(TEMPLATE_DIR, "static")

# ---------------- Flask App Setup ----------------
app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR
)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")
app.config['SESSION_TYPE'] = 'filesystem'

socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# ---------------- Database Setup ----------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://chylnx_hub_user:Qz7ERTTXsstDh2cpjMPWMobvdj3oKORQ@dpg-d3br27b7mgec739v7hd0-a.oregon-postgres.render.com/chylnx_hub"
)

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

db = get_db_connection()
cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if db else None

# ---------------- Initialize Database ----------------
def init_db():
    if not db:
        print("❌ No database connection")
        return
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
                user_id INT REFERENCES users(id),
                reference VARCHAR(255),
                amount NUMERIC(10,2),
                status VARCHAR(20) CHECK (status IN ('success','failed','pending')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
        print("✅ Database tables initialized")
    except Exception as e:
        print(f"❌ Database init failed: {e}")
        db.rollback()

if db:
    init_db()

# ---------------- Dummy in-memory storage ----------------
users_db = []  # temporary users storage
chat_locked = True  # default chat lock status

# ---------------- Helper ----------------
def execute_query(query, params=None):
    if not db:
        return None
    try:
        cursor.execute(query, params or ())
        if query.strip().upper().startswith("SELECT"):
            return cursor.fetchall()
        db.commit()
        return True
    except Exception as e:
        print(f"❌ Query failed: {e}")
        db.rollback()
        return None

# ---------------- Routes ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/payment")
def payment():
    return render_template("payment.html")

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        passcode = request.form.get("passcode")
        if passcode == "12345":  # example passcode
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Incorrect passcode", "error")
            return redirect(url_for("admin_login"))
    return render_template("admin_login.html")

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    # Prepare dummy user data with payment info
    users = []
    for u in users_db:
        users.append({
            "id": u.get("id"),
            "username": u.get("username"),
            "email": u.get("email"),
            "last_payment_status": u.get("last_payment_status", None),
            "last_payment_amount": u.get("last_payment_amount", None),
            "last_payment_date": u.get("last_payment_date", None)
        })

    return render_template("admin_dashboard.html", users=users, chat_locked=chat_locked)

@app.route("/toggle_chat_lock", methods=["POST"])
def toggle_chat_lock():
    global chat_locked
    chat_locked = not chat_locked
    return redirect(url_for("admin_dashboard"))

@app.route("/payment_required")
def payment_required():
    return render_template("payment_required.html")

@app.route("/set_username", methods=["GET", "POST"])
def set_username():
    if request.method == "POST":
        username = request.form.get("username")
        session["username"] = username
        return redirect(url_for("index"))
    username = session.get("username", "")
    return render_template("set_username.html", username=username)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")

        # Simple validation
        if any(u['username'] == username for u in users_db):
            return "Username already exists", 400

        user = {
            "id": str(uuid.uuid4())[:8],
            "username": username,
            "email": email,
            "password": generate_password_hash(password)
        }
        users_db.append(user)
        session["username"] = username
        return redirect(url_for("chat"))  # redirect to chat after registration

    return render_template("register.html")

@app.route("/chat")
def chat():
    username = session.get("username")
    if not username:
        return redirect(url_for("set_username"))
    return render_template("chat.html", username=username)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = next((u for u in users_db if u["username"] == username), None)
        if user and check_password_hash(user["password"], password):
            session["username"] = username
            return redirect(url_for("chat"))
        else:
            flash("Invalid credentials", "error")
            return redirect(url_for("login"))
    return render_template("login.html")

# ---------------- Socket.IO Chat ----------------
connected_users = {}  # username -> session_id
chat_history = []     # store all messages in memory

@socketio.on('join_chat')
def handle_join(data):
    username = data.get('username')
    if not username:
        return

    connected_users[username] = request.sid

    # Send chat history to this user
    emit('chat_history', chat_history, to=request.sid)

    # Notify everyone that a user joined
    emit('message', {
        'from': 'System',
        'text': f"{username} joined the chat!",
        'timestamp': datetime.utcnow().isoformat()
    }, broadcast=True)

    # Update online count
    emit('user_count_update', {'count': len(connected_users)}, broadcast=True)

@socketio.on('message')
def handle_message(data):
    # Identify sender
    username = None
    for user, sid in connected_users.items():
        if sid == request.sid:
            username = user
            break
    if not username:
        return

    msg_data = {
        'from': username,
        'text': data.get('text', ''),
        'timestamp': datetime.utcnow().isoformat()
    }

    # Save in history
    chat_history.append(msg_data)

    # Broadcast to everyone
    emit('message', msg_data, broadcast=True)

@socketio.on('announce_winner')
def handle_announcement(data):
    winners = data.get('winners', [])
    for winner in winners:
        emit('announcement', {'text': f"🏆 WINNER: {winner}!"}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    username = None
    for user, sid in list(connected_users.items()):
        if sid == request.sid:
            username = user
            del connected_users[user]
            break
    if username:
        emit('message', {
            'from': 'System',
            'text': f"{username} left the chat",
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        emit('user_count_update', {'count': len(connected_users)}, broadcast=True)

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
