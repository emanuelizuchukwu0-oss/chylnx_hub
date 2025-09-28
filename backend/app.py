import os
import uuid
from datetime import datetime
from flask import Flask, render_template, session, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import traceback

# ---------------- Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # chylnx_backend
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend", "chylnx_hub")
STATIC_DIR = os.path.join(TEMPLATE_DIR, "static")

# Print paths for debugging (Render logs)
print("🔎 BASE_DIR:", BASE_DIR)
print("🔎 TEMPLATE_DIR (templates):", TEMPLATE_DIR)
print("🔎 STATIC_DIR:", STATIC_DIR)

# sanity checks: list templates/static files if available (helps find missing index.html / images)
try:
    if os.path.isdir(TEMPLATE_DIR):
        print("📂 Templates folder contents:", os.listdir(TEMPLATE_DIR)[:50])
    else:
        print("❌ Templates folder not found at TEMPLATE_DIR")
    if os.path.isdir(STATIC_DIR):
        print("📂 Static folder contents:", os.listdir(STATIC_DIR)[:50])
    else:
        print("❌ Static folder not found at STATIC_DIR")
except Exception as e:
    print("⚠️ Error listing template/static dir:", e)

# ---------------- Flask ----------------
# Ensure Flask will look into TEMPLATE_DIR for render_template and serve static from STATIC_DIR
app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR
)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")
app.config['SESSION_TYPE'] = 'filesystem'

# ---------------- Socket.IO with optional Redis ----------------
REDIS_URL = os.getenv("REDIS_URL")  # e.g. redis://:password@host:port/0
if REDIS_URL:
    print("🔑 Using Redis message queue:", REDIS_URL)
    socketio = SocketIO(app, cors_allowed_origins="*", message_queue=REDIS_URL, manage_session=False)
else:
    socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# ---------------- Database ----------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    # keep default if you want (replace for production)
    "postgresql://chylnx_hub_user:Qz7ERTTXsstDh2cpjMPWMobvdj3oKORQ@dpg-d3br27b7mgec739v7hd0-a/chylnx_hub"
)

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

def execute_query(query, params=None, fetch=False):
    """Execute a query and optionally fetch results; always opens/closes connection."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(query, params or ())
            if fetch:
                result = cursor.fetchall()
                conn.commit()
                return result
            conn.commit()
            return True
    except Exception as e:
        print(f"❌ Query failed: {e}")
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
        return None
    finally:
        conn.close()

# ---------------- Initialize Tables ----------------
def init_db():
    conn = get_db_connection()
    if not conn:
        print("❌ init_db: no DB connection available")
        return
    try:
        with conn.cursor() as cursor:
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
        conn.commit()
        print("✅ Database tables initialized")
    except Exception as e:
        print("❌ Database init failed:", e)
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
    finally:
        conn.close()

init_db()

# ---------------- App Logic ----------------
chat_locked = True  # default

@app.errorhandler(500)
def internal_error(e):
    # Log stack trace
    print("❌ Internal Server Error:", e)
    traceback.print_exc()
    return "Internal server error", 500

# ---------------- Routes ----------------
@app.route("/")
def index():
    # render index.html from TEMPLATE_DIR
    return render_template("index.html")

@app.route("/payment")
def payment():
    return render_template("payment.html")

@app.route("/payment_required")
def payment_required():
    return render_template("payment_required.html")

@app.route("/set_username", methods=["GET", "POST"])
def set_username():
    # single endpoint only (no duplicate)
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if not username:
            flash("Please provide a username", "error")
            return redirect(url_for("set_username"))

        # Try to find user
        existing = execute_query("SELECT * FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if not existing:
            # create user
            execute_query("INSERT INTO users (username) VALUES (%s)", (username,))
            existing = execute_query("SELECT * FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
        if existing:
            user = existing[0]
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Username set successfully!", "success")
            return redirect(url_for("chat"))

        flash("Could not set username, try again", "error")
        return redirect(url_for("set_username"))

    return render_template("set_username.html")

@app.route("/chat")
def chat():
    # Make sure user selected username
    if not session.get("user_id") or not session.get("username"):
        return redirect(url_for("set_username"))

    username = session["username"]

    # Check payment
    paid = execute_query("""
        SELECT p.id FROM payments p
        JOIN users u ON p.user_id = u.id
        WHERE u.username = %s AND p.status = 'success'
        LIMIT 1
    """, (username,), fetch=True)

    if not paid:
        return redirect(url_for("payment_required"))

    return render_template("chat.html", username=username)

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
            flash("Username or email already exists", "error")
            return redirect(url_for("register"))

        execute_query("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
                      (username, email, generate_password_hash(password)))
        session["username"] = username
        flash("Registration successful", "success")
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
                flash("Login successful", "success")
                return redirect(url_for("chat"))
        flash("Invalid credentials", "error")
        return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        passcode = request.form.get("passcode")
        if passcode == "12345":
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect passcode", "error")
        return redirect(url_for("admin_login"))
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

# ---------------- Socket.IO ----------------
connected_users = {}  # username -> {sid, user_id}

@socketio.on("join_chat")
def handle_join(data):
    username = data.get("username")
    if not username:
        return

    user = execute_query("SELECT id FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)
    if not user:
        return
    user_id = user[0]["id"]

    connected_users[username] = {"sid": request.sid, "user_id": user_id}

    history = execute_query("""
        SELECT u.username AS "from", m.message AS text, m.created_at AS timestamp
        FROM messages m
        JOIN users u ON m.user_id = u.id
        ORDER BY m.created_at ASC
        LIMIT 500
    """, fetch=True) or []

    emit("chat_history", history, to=request.sid)
    emit("message", {"from": "System", "text": f"{username} joined!", "timestamp": datetime.utcnow().isoformat()}, broadcast=True)
    emit("user_count_update", {"count": len(connected_users)}, broadcast=True)

@socketio.on("message")
def handle_message(data):
    username = None
    user_id = None
    for u, info in connected_users.items():
        if info["sid"] == request.sid:
            username, user_id = u, info["user_id"]
            break
    if not username or not user_id:
        return

    text = (data.get("text") or "").strip()
    if not text:
        return

    execute_query("INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s)", (user_id, username, text))
    emit("message", {"from": username, "text": text, "timestamp": datetime.utcnow().isoformat()}, broadcast=True)

@socketio.on("announce_winner")
def handle_announcement(data):
    winners = data.get("winners", [])
    for winner in winners:
        emit("announcement", {'text': f"🏆 WINNER: {winner}!"}, broadcast=True)

@socketio.on("disconnect")
def handle_disconnect():
    username = None
    for u, info in list(connected_users.items()):
        if info["sid"] == request.sid:
            username = u
            del connected_users[u]
            break
    if username:
        emit("message", {"from": "System", "text": f"{username} left", "timestamp": datetime.utcnow().isoformat()}, broadcast=True)
        emit("user_count_update", {"count": len(connected_users)}, broadcast=True)

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
