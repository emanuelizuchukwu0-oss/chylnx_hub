from flask import Flask, render_template

# Tell Flask where your HTML and static files are
app = Flask(__name__, template_folder="frontend", static_folder="static")

# Home Page
@app.route("/")
def index():
    return render_template("index.html")

# Admin Panel
@app.route("/admin")
def admin():
    return render_template("admin.html")

# Payment Page
@app.route("/payment")
def payment():
    return render_template("payment.html")

# Chat Page
@app.route("/chat")
def chat():
    return render_template("chat.html")

# Game Page (optional if you have game.html)
@app.route("/game")
def game():
    return render_template("game.html")

if __name__ == "__main__":
    app.run(debug=True)
