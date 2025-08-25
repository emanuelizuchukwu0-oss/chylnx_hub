from flask import Flask, render_template, request, session, jsonify
from flask_session import Session

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Dummy storage
payments = []
chat_messages = []

ADMIN_USER = "admin"

# --- ROUTES ---

# Home page
@app.route('/')
def home():
    return render_template("index.html")

# Payment page
@app.route('/payment', methods=['GET'])
def payment_page():
    return render_template("payment.html")

# Handle payment (dummy)
@app.route('/pay', methods=['POST'])
def pay():
    data = request.get_json()
    user = data.get("user")
    amount = data.get("amount")
    session[user] = True  # mark as paid
    payments.append({"user": user, "amount": amount})
    return jsonify({"status": "success"})

# Chat page (only for paid users or admin)
@app.route('/chat')
def chat_page():
    user = request.args.get("user")
    if user == ADMIN_USER or session.get(user):
        return render_template("chat.html", username=user)
    else:
        return "❌ You must pay to access chat", 403

# Admin panel
@app.route('/admin')
def admin_panel():
    return render_template("admin.html")

# API: get all payments
@app.route('/payments', methods=['GET'])
def get_payments():
    return jsonify(payments)

# API: get chat messages
@app.route('/chatMessages', methods=['GET'])
def get_chat_messages():
    return jsonify(chat_messages)

# API: send chat messages
@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user = data.get("user")
    message = data.get("message")
    chat_messages.append({"user": user, "message": message})
    return jsonify({"status": "success"})

if __name__ == "__main__":
    app.run(debug=True)
from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def home():
    return render_template("index.html")  # Flask looks in templates/
chat_messages = []

@app.route('/chatMessages', methods=['GET'])
def get_chat_messages():
    return jsonify(chat_messages)

@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user = data.get("user")
    message = data.get("message")
    chat_messages.append({"user": user, "message": message})
    return jsonify({"status": "success"})
from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')  # make sure this is pointing to your updated index.html
@app.route('/chat')
def chat():
    user = request.args.get('user', 'player1')
    # Check if user has paid
    if not paid_users.get(user):
        return "You need to pay to access the chat", 403
    return render_template('chat.html')
from flask import Flask, render_template, request, redirect, session, jsonify

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

# Store chat messages
chat_messages = []

# Simulate paid users
paid_users = {"player1": True}  # later we can update after payment
@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.get_json()
    user = data.get('user', 'player1')
    message = data.get('message')
    if user in paid_users:
        chat_messages.append({"user": user, "message": message})
        return jsonify({"status": "success"})
    return jsonify({"status": "fail"}), 403

@app.route('/get_messages')
def get_messages():
    return jsonify(chat_messages)
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

# Store chat messages
chat_messages = []

# Simulate paid users
paid_users = {"player1": True}

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/chat')
def chat():
    user = request.args.get('user', 'player1')
    if not paid_users.get(user):
        return "You need to pay to access the chat", 403
    return render_template('chat.html')

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.get_json()
    user = data.get('user', 'player1')
    message = data.get('message')
    if user in paid_users:
        chat_messages.append({"user": user, "message": message})
        return jsonify({"status": "success"})
    return jsonify({"status": "fail"}), 403

@app.route('/get_messages')
def get_messages():
    return jsonify(chat_messages)

if __name__ == "__main__":
    app.run(debug=True)
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

chat_messages = []  # store messages in memory
paid_users = {"player1": True}  # allow access for testing

@app.route('/chat')
def chat():
    user = request.args.get('user', 'player1')
    if not paid_users.get(user):
        return "You need to pay to access the chat", 403
    return render_template('chat.html')

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.get_json()
    user = data.get('user', 'player1')
    message = data.get('message')
    if user in paid_users:
        chat_messages.append({"user": user, "message": message})
        return jsonify({"status": "success"})
    return jsonify({"status": "fail"}), 403

@app.route('/get_messages')
def get_messages():
    return jsonify(chat_messages)

if __name__ == "__main__":
    app.run(debug=True)
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

# Store messages (for testing only, not for production!)
messages = []

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form.get("username")
        message = request.form.get("message")
        if username and message:
            messages.append({"username": username, "message": message})
        return redirect(url_for("index"))
    return render_template("index.html", messages=messages)

if __name__ == "__main__":
    app.run(debug=True)
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

SECRET_KEY = "sk_test_sk_test_66406f3ab66b22bd2d3ec8bbbb609e70371949f6"  # Replace with your Secret Key

@app.route('/verify-payment/<reference>', methods=['GET'])
def verify_payment(reference):
    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {
        "Authorization": f"Bearer {SECRET_KEY}"
    }
    response = requests.get(url, headers=headers)
    data = response.json()

    if data["status"] and data["data"]["status"] == "success":
        return jsonify({"status": "success", "data": data["data"]})
    else:
        return jsonify({"status": "failed", "data": data})
@app.route("/payment")
def payment():
    return render_template("payment.html")
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

messages = []
chat_locked = False
winner_message = None

@app.route("/send", methods=["POST"])
def send():
    global messages
    data = request.json
    messages.append({"sender": "player", "text": data["message"]})
    return jsonify({"status": "ok"})

@app.route("/messages")
def get_messages():
    return jsonify({
        "messages": messages,
        "chatLocked": chat_locked,
        "winner": winner_message
    })

@app.route("/lock", methods=["POST"])
def lock():
    global chat_locked
    chat_locked = True
    return jsonify({"status": "chat locked"})

@app.route("/unlock", methods=["POST"])
def unlock():
    global chat_locked
    chat_locked = False
    return jsonify({"status": "chat unlocked"})

@app.route("/winner", methods=["POST"])
def winner():
    global winner_message
    winner_message = "🏆 Congratulations! You are a winner!"
    return jsonify({"status": "winner announced"})

if __name__ == "__main__":
    app.run(debug=True)
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, send, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

# Control if chat is unlocked
chat_unlocked = False

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/start_game", methods=["POST"])
def start_game():
    global chat_unlocked
    chat_unlocked = True
    return jsonify({"status": "✅ game started, chat unlocked"})

@app.route("/stop_game", methods=["POST"])
def stop_game():
    global chat_unlocked
    chat_unlocked = False
    return jsonify({"status": "⛔ game stopped, chat locked"})

@app.route("/chat")
def chat():
    if not chat_unlocked:
        return "<h1>❌ You must pay to access chat</h1>", 403
    return render_template("chat.html")   # your WhatsApp-style chat page

# --- SocketIO Chat ---
@socketio.on('message')
def handle_message(msg):
    global chat_unlocked
    if not chat_unlocked:
        send("❌ Chat is locked. Please wait for game start.", broadcast=False)
        return
    print("📩 Message received:", msg)
    send(msg, broadcast=True)  # broadcast to everyone

if __name__ == "__main__":
    socketio.run(app, debug=True)
    from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "Hello, CHYLNX is live!"

if __name__ == "__main__":
    app.run()
