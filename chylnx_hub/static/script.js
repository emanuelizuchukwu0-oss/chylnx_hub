// Connect to Flask-SocketIO
const socket = io();

// Elements
const chatWindow = document.getElementById("chatWindow");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

let myId = null;

// Get your own ID from server
socket.on("connect", () => {
  myId = socket.id;
});

// Send message
function sendMessage() {
  const msg = chatInput.value.trim();
  if (!msg) return;

  // Show immediately in chat
  let div = document.createElement("div");
  div.classList.add("msg", "me");
  div.textContent = msg;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;

  // Send to server
  socket.emit("message", { id: myId, text: msg });
  chatInput.value = "";
}

// Send on button click
sendBtn.addEventListener("click", sendMessage);

// Send on Enter key
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Receive messages
socket.on("message", (data) => {
  if (data.id === myId) return; // avoid showing own msg again

  let div = document.createElement("div");
  div.classList.add("msg", "other");
  div.textContent = data.text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
});
