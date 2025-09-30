# backend/app.py
import os
import uuid
import traceback
from datetime import datetime, timedelta
import requests

from flask import Flask, render_template, session, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras

# ---------------- Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # chylnx_backend
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend")
STATIC_DIR = os.path.join(TEMPLATE_DIR, "static")

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
    print("⚠️ Error listing template/static:", e)

# ---------------- Flask ----------------
app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR
)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "secret123")
app.config['SESSION_TYPE'] = 'filesystem'

# ---------------- Socket.IO (with optional Redis) ----------------
REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL:
    print("🔑 Using Redis message queue:", REDIS_URL)
    socketio = SocketIO(app, cors_allowed_origins="*", message_queue=REDIS_URL, manage_session=False)
else:
    socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# ---------------- Database ----------------
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_LOCAL")  # fallback if needed
print("🔎 DATABASE_URL set:", bool(DATABASE_URL))

def get_db_connection():
    if not DATABASE_URL:
        print("❌ No DATABASE_URL configured")
        return None
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

# ---------------- Initialize DB ----------------
def init_db():
    conn = get_db_connection()
    if not conn:
        print("❌ init_db: no DB connection")
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
                    user_id INT,
                    reference VARCHAR(255) UNIQUE,
                    amount NUMERIC(10,2),
                    status VARCHAR(20) CHECK (status IN ('success','failed','pending')) DEFAULT 'pending',
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
        conn.commit()
        print("✅ Tables initialized")
    except Exception as e:
        print("❌ DB init failed:", e)
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
    finally:
        conn.close()

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
    execute_query("INSERT INTO game_timer (end_time, is_running) VALUES (%s, %s)", (end_time, True))
    return end_time

def get_remaining_time():
    timer = get_current_timer()
    if not timer:
        return None
    end_time = timer['end_time']
    now = datetime.now()
    if now >= end_time:
        execute_query("DELETE FROM game_timer WHERE id = %s", (timer['id'],))
        return 0
    remaining = (end_time - now).total_seconds()
    return max(0, int(remaining))

# ---------------- App Logic ----------------
chat_locked = True

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
            execute_query("INSERT INTO users (username) VALUES (%s)", (username,))
            existing = execute_query("SELECT * FROM users WHERE username=%s LIMIT 1", (username,), fetch=True)

        if existing:
            user = existing[0]
            session["user_id"] = user["id"]
            session["username"] = user["username"]
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

# ---------------- Socket.IO ----------------
connected_users = {}  # username -> {"sid": <sid>, "user_id": <id>}

@socketio.on("connect")
def handle_connect():
    remaining = get_remaining_time()
    print(f"🔔 Client connected. Remaining time: {remaining}")
    if remaining is not None and remaining > 0:
        emit('timer_update', {'remaining_seconds': remaining, 'is_running': True})
    else:
        emit('timer_update', {'remaining_seconds': 0, 'is_running': False})

@socketio.on("set_timer")
def handle_set_timer(data):
    try:
        minutes = int(data.get('minutes', 5))
        seconds = int(data.get('seconds', 0))
        if minutes == 0 and seconds == 0:
            emit('timer_error', {'message': 'Please set a valid timer duration'})
            return
        end_time = set_timer(minutes, seconds)
        remaining = get_remaining_time()
        emit('timer_update', {'remaining_seconds': remaining, 'is_running': True}, broadcast=True)
    except Exception as e:
        print("❌ Timer error:", e)
        emit('timer_error', {'message': str(e)})

@socketio.on("get_timer")
def handle_get_timer():
    remaining = get_remaining_time()
    if remaining is not None and remaining > 0:
        emit('timer_update', {'remaining_seconds': remaining, 'is_running': True})
    else:
        emit('timer_update', {'remaining_seconds': 0, 'is_running': False})

connected_users = {}  # {username: {sid, user_id}}

@socketio.on("join")
def handle_join(data):
    username = data.get("username")
    room = data.get("room")

    join_room(room)

    # load history for this room
    history = load_chat_history(room)

    # safer emit
    emit("chat_history", history, room=request.sid)


@socketio.on("send_message")
def handle_message(data):
    try:
        print("📨 Message received (raw):", data)

        # Find sender
        username, user_id = None, None
        for u, info in connected_users.items():
            if info.get("sid") == request.sid:
                username, user_id = u, info.get("user_id")
                break

        if not username:
            username = data.get("from")
        if not username:
            print("⚠️ Could not determine sender for sid:", request.sid)
            return

        text = (data.get("text") or "").strip()
        if not text:
            return

        # Save to DB
        if user_id:
            execute_query(
                "INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s)",
                (user_id, username, text)
            )

        msg = {
            "from": username,
            "text": text,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Broadcast to all clients (including sender for simplicity)
        emit("receive_message", msg, broadcast=True)

    except Exception as e:
        print("❌ handle_message error:", e)
        traceback.print_exc()


@socketio.on("disconnect")
def handle_disconnect():
    username_to_remove = None
    for username, info in list(connected_users.items()):
        if info.get("sid") == request.sid:
            username_to_remove = username
            break

    if username_to_remove:
        del connected_users[username_to_remove]
        print(f"❌ {username_to_remove} left, total users: {len(connected_users)}")

        # Just update the count (no system messages)
        socketio.emit("user_count_update", {"count": len(connected_users)}, broadcast=True)

# ---------------- Payment Routes ----------------
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
print("🔑 PAYSTACK_SECRET_KEY present:", bool(PAYSTACK_SECRET_KEY))

@app.route("/initialize_payment", methods=["POST"])
def initialize_payment():
    """Initialize payment with Paystack (JSON API)"""
    try:
        user_id = session.get("user_id")
        username = session.get("username")

        if not user_id or not username:
            return jsonify({"error": "User not logged in"}), 401

        reference = str(uuid.uuid4())
        amount_kobo = 50000  # 500 NGN in kobo

        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "email": f"{username}@chylnx.com",
            "amount": amount_kobo,
            "reference": reference,
            "callback_url": f"{request.host_url}payment_verify",
            "metadata": {
                "user_id": user_id,
                "username": username
            }
        }

        resp = requests.post("https://api.paystack.co/transaction/initialize", headers=headers, json=payload)
        result = resp.json() if resp.content else {"status": False, "message": "empty response"}
        print("🔎 Paystack init response:", result)

        if resp.status_code == 200 and result.get("status"):
            # store pending payment (amount in Naira)
            execute_query(
                "INSERT INTO payments (user_id, reference, amount, status) VALUES (%s, %s, %s, %s)",
                (user_id, reference, amount_kobo / 100.0, "pending")
            )
            return jsonify({
                "authorization_url": result['data']['authorization_url'],
                "reference": reference
            })
        else:
            return jsonify({"error": result.get("message", "Payment init failed")}), 400

    except Exception as e:
        print("❌ Payment initialization error:", e)
        traceback.print_exc()
        return jsonify({"error": "Payment initialization failed"}), 500

@app.route("/payment_verify", methods=["GET"])
def payment_verify():
    """Verify payment after Paystack redirect (JSON API)"""
    try:
        reference = request.args.get('reference') or request.args.get('trxref')
        if not reference:
            return jsonify({"error": "No reference provided"}), 400

        headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Content-Type": "application/json"}
        resp = requests.get(f"https://api.paystack.co/transaction/verify/{reference}", headers=headers)
        result = resp.json() if resp.content else {"status": False, "message": "empty response"}
        print("🔎 Paystack verify response:", result)

        if resp.status_code == 200 and result.get("status"):
            data = result["data"]
            user_id = data.get("metadata", {}).get("user_id") or session.get("user_id")
            amount = data.get("amount", 0) / 100.0

            if data.get("status") == "success":
                execute_query("UPDATE payments SET status=%s WHERE reference=%s", ("success", reference))
                # ensure user's paid flag
                session['paid'] = True
                # If you want to redirect back to a frontend page:
                # return redirect(url_for('chat'))
                return jsonify({"success": True, "amount": amount, "reference": reference, "user_id": user_id})
            else:
                execute_query("UPDATE payments SET status=%s WHERE reference=%s", ("failed", reference))
                return jsonify({"success": False, "message": "Payment failed"}), 400
        else:
            return jsonify({"success": False, "message": result.get("message", "Verification failed")}), 400

    except Exception as e:
        print("❌ Payment verification error:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": "Payment verification failed"}), 500

@app.route("/check_payment_status")
def check_payment_status():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"paid": False})
    payment = execute_query(
        "SELECT * FROM payments WHERE user_id = %s AND status = 'success' LIMIT 1",
        (user_id,), fetch=True
    )
    return jsonify({"paid": bool(payment)})

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)
