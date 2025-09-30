import os
import uuid
import traceback
from datetime import datetime, timedelta
from flask import Flask, render_template, session, request, redirect, url_for, jsonify, flash
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras

# ---------------- Paths ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # chylnx_backend
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
                    user_id INT REFERENCES users(id),
                    reference VARCHAR(255),
                    amount NUMERIC(10,2),
                    status VARCHAR(20) CHECK (status IN ('success','failed','pending')),
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

init_db()

# ---------------- Timer Functions ----------------
def get_current_timer():
    """Get the active timer from database"""
    result = execute_query(
        "SELECT * FROM game_timer WHERE is_running = TRUE ORDER BY created_at DESC LIMIT 1",
        fetch=True
    )
    if result:
        return result[0]
    return None

def set_timer(minutes, seconds):
    """Set a new timer in the database"""
    total_seconds = (minutes * 60) + seconds
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    
    # Clear ALL existing timers first
    execute_query("DELETE FROM game_timer")
    
    # Create new timer
    execute_query(
        "INSERT INTO game_timer (end_time, is_running) VALUES (%s, %s)",
        (end_time, True)
    )
    
    return end_time

def get_remaining_time():
    """Calculate remaining time for active timer"""
    timer = get_current_timer()
    if not timer:
        return None
    
    end_time = timer['end_time']
    now = datetime.now()
    
    if now >= end_time:
        # Timer expired, delete it
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
connected_users = {}

@socketio.on("connect")
def handle_connect():
    """Send current timer state when client connects"""
    remaining = get_remaining_time()
    print(f"🔔 Client connected. Remaining time: {remaining}")
    
    if remaining is not None and remaining > 0:
        emit('timer_update', {
            'remaining_seconds': remaining,
            'is_running': True
        })
    else:
        # No active timer or timer expired
        emit('timer_update', {
            'remaining_seconds': 0,
            'is_running': False
        })

@socketio.on("set_timer")
def handle_set_timer(data):
    """Handle timer setting from admin"""
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
        
        # Broadcast to all clients
        emit('timer_update', {
            'remaining_seconds': remaining,
            'is_running': True
        }, broadcast=True)
        
    except Exception as e:
        print(f"❌ Timer setting error: {e}")
        emit('timer_error', {'message': str(e)})

@socketio.on("get_timer")
def handle_get_timer():
    """Send current timer state to requesting client"""
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
    emit("message", {"from": "System", "text": f"{username} joined!"}, broadcast=True)
    emit("user_count_update", {"count": len(connected_users)}, broadcast=True)

@socketio.on("message")
def handle_message(data):
    try:
        print("📨 Message received (raw):", data)

        # Find sender by request.sid (authoritative)
        username = None
        user_id = None
        for u, info in connected_users.items():
            if info["sid"] == request.sid:
                username = u
                user_id = info.get("user_id")
                break

        # Fall back to data['from'] only if not found (defensive)
        if not username:
            username = data.get("from")
        if not username:
            # Can't determine sender — ignore to be safe
            print("⚠️ Could not determine message sender for sid:", request.sid)
            return

        text = (data.get("text") or "").strip()
        if not text:
            # ignore empty messages
            return

        # Save to DB if we know user_id (optional if you want all messages saved)
        if user_id:
            execute_query(
                "INSERT INTO messages (user_id, username, message) VALUES (%s, %s, %s)",
                (user_id, username, text)
            )

        # Build canonical message object
        msg = {
            "from": username,
            "text": text,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Broadcast to other clients but NOT to the sender (sender already added locally)
        # include_self=False prevents sender from receiving the broadcast back
        emit("message", msg, broadcast=True, include_self=True)


        # (Optional) still send an ack to sender if you want server-validated message id
        # emit("message_ack", {"status": "ok", "timestamp": msg["timestamp"]}, to=request.sid)

    except Exception as e:
        print("❌ handle_message error:", e)
        traceback.print_exc()


@socketio.on("disconnect")
def handle_disconnect():
    username = None
    for u, info in list(connected_users.items()):
        if info["sid"] == request.sid:
            username = u
            del connected_users[u]
            break
    if username:
        emit("message", {"from": "System", "text": f"{username} left"}, broadcast=True)
        emit("user_count_update", {"count": len(connected_users)}, broadcast=True)

# Add this import at the top with other imports
import requests

# Add this after your other routes, before Socket.IO section
# ---------------- Payment Routes ----------------

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_...")

@app.route("/initialize_payment", methods=["POST"])
def initialize_payment():
    """Initialize payment with Paystack"""
    try:
        # Get user info from session
        user_id = session.get("user_id")
        username = session.get("username")
        
        if not user_id:
            return jsonify({"error": "User not logged in"}), 401
        
        # Generate unique reference
        reference = str(uuid.uuid4())
        amount = 50000  # 500 Naira in kobo
        
        # Initialize payment with Paystack
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "email": f"{username}@chylnx.com",  # Use username as email base
            "amount": amount,
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
            # Store payment reference in session or database
            session['payment_reference'] = reference
            
            return jsonify({
                "authorization_url": result['data']['authorization_url'],
                "reference": reference
            })
        else:
            return jsonify({"error": "Payment initialization failed"}), 400
            
    except Exception as e:
        print(f"❌ Payment initialization error: {e}")
        return jsonify({"error": "Payment initialization failed"}), 500

@app.route("/payment_verify")
def payment_verify():
    """Verify payment after Paystack redirect"""
    try:
        reference = request.args.get('reference')
        trxref = request.args.get('trxref')
        
        # Use the reference from URL or session
        payment_ref = reference or trxref or session.get('payment_reference')
        
        if not payment_ref:
            flash("Payment verification failed: No reference found", "error")
            return redirect(url_for("payment"))
        
        # Verify payment with Paystack
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
            
            if result['data']['status'] == 'success':
                # Payment successful
                user_id = session.get("user_id")
                amount = result['data']['amount'] / 100  # Convert from kobo to Naira
                
                # Store payment in database
                execute_query(
                    "INSERT INTO payments (user_id, reference, amount, status) VALUES (%s, %s, %s, %s)",
                    (user_id, payment_ref, amount, 'success')
                )
                
                # Unlock chat for user
                session['paid'] = True
                flash("Payment successful! You can now access the chat.", "success")
                return redirect(url_for("chat"))
            else:
                flash("Payment verification failed. Please try again or contact support.", "error")
                return redirect(url_for("payment"))
        else:
            flash("Payment verification failed. Please try again or contact support.", "error")
            return redirect(url_for("payment"))
            
    except Exception as e:
        print(f"❌ Payment verification error: {e}")
        flash("Payment verification failed. Please try again or contact support.", "error")
        return redirect(url_for("payment"))

@app.route("/check_payment_status")
def check_payment_status():
    """Check if current user has paid"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"paid": False})
    
    # Check if user has a successful payment
    payment = execute_query(
        "SELECT * FROM payments WHERE user_id = %s AND status = 'success' LIMIT 1",
        (user_id,), fetch=True
    )
    
    if payment:
        session['paid'] = True
        return jsonify({"paid": True})
    else:
        return jsonify({"paid": False})

connected_users = {}  # username -> sid

@socketio.on("join_chat")
def handle_join(data):
    username = data.get("username")
    if not username:
        return

    # Save username + sid
    connected_users[username] = request.sid
    print(f"✅ {username} joined, total users: {len(connected_users)}")

    # Broadcast updated user count
    socketio.emit("user_count_update", {"count": len(connected_users)})


@socketio.on("disconnect")
def handle_disconnect():
    # Find user by sid
    username_to_remove = None
    for username, sid in list(connected_users.items()):
        if sid == request.sid:
            username_to_remove = username
            break

    if username_to_remove:
        del connected_users[username_to_remove]
        print(f"❌ {username_to_remove} left, total users: {len(connected_users)}")

    # Broadcast updated user count
    socketio.emit("user_count_update", {"count": len(connected_users)})


# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)