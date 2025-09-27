import os
from datetime import datetime, timedelta
from flask import Flask, render_template, session, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import requests

# ---------------- Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'chylnx_hub', 'html files')  # Your HTML files
STATIC_DIR = os.path.join(BASE_DIR, 'chylnx_hub', 'static')        # Your static files

# ---------------- App Setup ----------------
app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR
)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")
app.config['SESSION_TYPE'] = 'filesystem'

socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_...")

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
        return False
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE,
                password VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                session_code VARCHAR(255) UNIQUE NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status VARCHAR(50) DEFAULT 'active'
            );
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
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id INT REFERENCES users(id) ON DELETE CASCADE,
                username VARCHAR(255),
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        db.commit()
        print("✅ Database tables initialized")
        return True
    except Exception as e:
        print(f"❌ Database init failed: {e}")
        return False

if db:
    init_db()
else:
    print("⚠️ Skipping DB init - no connection")

# ---------------- Globals ----------------
chat_locked = False
next_game_time = datetime.utcnow() + timedelta(minutes=2, seconds=15)
connected_users = {}

# ---------------- Helper Functions ----------------
def execute_query(query, params=None):
    if not db: return None
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
    try:
        username = session.get("username")
        paid = session.get("paid", False)
        if db and username:
            result = execute_query("""
                SELECT p.id FROM payments p
                JOIN users u ON p.user_id = u.id
                WHERE u.username = %s AND p.status='success' LIMIT 1
            """, (username,))
            paid = paid or bool(result)
        return render_template("index.html", paid=paid)
    except Exception as e:
        print(f"❌ Index error: {e}")
        return render_template("index.html", paid=False)

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "database": "connected" if db else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    })

# ---------------- User Routes ----------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        username = request.form.get("username","").strip()
        email = request.form.get("email","").strip()
        password = request.form.get("password","").strip()
        if not username or not email or not password:
            return "❌ All fields required"
        if db:
            if execute_query("SELECT id FROM users WHERE email=%s", (email,)):
                return "❌ Email exists"
            execute_query("INSERT INTO users (username,email,password) VALUES (%s,%s,%s)", 
                          (username,email,generate_password_hash(password)))
            user = execute_query("SELECT id FROM users WHERE email=%s", (email,))
            if user:
                session["user_id"] = user[0]["id"]
                session["username"] = username
                return redirect(url_for("chat"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","").strip()
        if db:
            result = execute_query("SELECT * FROM users WHERE email=%s",(email,))
            if result and check_password_hash(result[0]["password"], password):
                session["user_id"] = result[0]["id"]
                session["username"] = result[0]["username"]
                return redirect(url_for("chat"))
        return "❌ Invalid credentials"
    return render_template("login.html")

# ---------------- Chat Routes ----------------
@app.route("/username", methods=["GET","POST"])
def username_route():
    if request.method=="POST":
        name = request.form.get("username","").strip()
        if name:
            session["username"] = name
            if db:
                user = execute_query("SELECT id FROM users WHERE username=%s",(name,))
                if not user:
                    execute_query("INSERT INTO users (username) VALUES (%s)", (name,))
                    user = execute_query("SELECT id FROM users WHERE username=%s",(name,))
                if user:
                    session["user_id"] = user[0]["id"]
        return redirect(url_for("chat"))
    return render_template("username.html", username=session.get("username",""))

@app.route("/chat")
def chat():
    username = session.get("username")
    if not username:
        return redirect(url_for("username_route"))
    paid = session.get("paid", False)
    if db and not paid:
        result = execute_query("""
            SELECT p.id FROM payments p 
            JOIN users u ON p.user_id=u.id
            WHERE u.username=%s AND p.status='success' LIMIT 1
        """, (username,))
        paid = bool(result)
    if not paid:
        return render_template("payment.html")
    global chat_locked
    if chat_locked:
        return render_template("locked.html")
    return render_template("chat.html", username=username, paid=paid)

# ---------------- SocketIO Events ----------------
@socketio.on("connect")
def on_connect():
    username = session.get("username", f"Guest-{request.sid[:5]}")
    connected_users[request.sid] = username
    print(f"✅ {username} connected")
    if db:
        history = execute_query("SELECT username,message,created_at FROM messages ORDER BY created_at DESC LIMIT 50") or []
        emit("chat_history", history[::-1])
    emit("chat_status", {"locked": chat_locked})

@socketio.on("message")
def on_message(data):
    username = connected_users.get(request.sid)
    if not username or not db: return
    text = data.get("text","").strip()
    if not text: return
    user_id = execute_query("SELECT id FROM users WHERE username=%s",(username,))
    if not user_id: return
    execute_query("INSERT INTO messages (user_id,username,message) VALUES (%s,%s,%s)", 
                  (user_id[0]["id"], username, text))
    new_msg = execute_query("SELECT username,message,created_at FROM messages ORDER BY id DESC LIMIT 1")
    if new_msg:
        emit("message", {
            "from": new_msg[0]["username"],
            "text": new_msg[0]["message"],
            "timestamp": new_msg[0]["created_at"].isoformat()
        }, broadcast=True)

# ---------------- Run ----------------
if __name__=="__main__":
    port = int(os.getenv("PORT",5000))
    debug = os.getenv("DEBUG","false").lower()=="true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
