import os
from datetime import datetime, timedelta
from flask import Flask, render_template, session, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import requests

# ---------------- Absolute Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # chylnx_backend
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend", "chylnx_hub")  # <-- folder containing index.html
STATIC_DIR = os.path.join(BASE_DIR, "frontend", "chylnx_hub", "static")  # <-- your static files

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
    "postgresql://chylnx_hub_user:password@your-render-host/chylnx_hub"
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


@app.route("/chat")
def chat():
    return render_template("chat.html")  # Make sure chat.html is also inside chylnx_hub/index

@app.route("/payment")
def payment():
    return render_template("payment.html")  # Make sure payment.html is in the same folder


# Health check for Render
@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "database": "connected" if db else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    })

# ---------------- SocketIO Events ----------------
connected_users = {}

@socketio.on("connect")
def handle_connect():
    username = session.get("username", f"Guest-{request.sid[:5]}")
    connected_users[request.sid] = username
    print(f"✅ {username} connected")
    emit("chat_status", {"locked": False})

@socketio.on("message")
def handle_message(data):
    username = connected_users.get(request.sid, "Unknown")
    msg_text = data.get("text","").strip()
    if not msg_text:
        return
    if db:
        user_id = execute_query("SELECT id FROM users WHERE username=%s", (username,))
        execute_query(
            "INSERT INTO messages (user_id, username, message) VALUES (%s,%s,%s)",
            (user_id[0]['id'] if user_id else None, username, msg_text)
        )
    emit("message", {"from": username, "text": msg_text}, broadcast=True)

@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    if sid in connected_users:
        user = connected_users.pop(sid)
        print(f"❌ {user} disconnected gracefully")


# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
