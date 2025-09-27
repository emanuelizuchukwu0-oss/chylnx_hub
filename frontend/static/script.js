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

// Send messagesocket.on("message", (data) => {
//   data will be { id: uuid, from: username, text: "...", timestamp: "..." }
//   show message regardless of sender; style by comparing data.from to your username
//   const div = document.createElement("div");
//   div.classList.add("msg", (data.from === myUsername ? "me" : "other"));
//   div.textContent = `${data.from}: ${data.text}`;
//   chatWindow.appendChild(div);
//   chatWindow.scrollTop = chatWindow.scrollHeight;
// });

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

// Example listener for payment unlock (FIXED: no random data)
socket.on("payment_status", (data) => {
  if (data.status === "success") {
    document.getElementById("payOverlay").innerHTML = "🎉 Chat Unlocked!";
    setTimeout(() => {
      document.getElementById("payOverlay").style.display = "none";
      chatInput.disabled = false;
      sendBtn.disabled = false;
    }, 1500);
  }
});

// 🔒 Lock/Unlock Buttons (only show if admin)
// NOTE: This line must be inside a Jinja-rendered HTML file
const isAdmin = `{{ 'true' if session.get('is_admin') else 'false' }}`;

if (isAdmin) {
  const lockBtn = document.createElement("button");
  lockBtn.textContent = "🔒 Lock Chat";
  lockBtn.style.marginLeft = "10px";

  const unlockBtn = document.createElement("button");
  unlockBtn.textContent = "🔓 Unlock Chat";
  unlockBtn.style.marginLeft = "5px";

  document.querySelector("header").appendChild(lockBtn);
  document.querySelector("header").appendChild(unlockBtn);

  lockBtn.addEventListener("click", () => {
    socket.emit("lock_chat");
  });
  unlockBtn.addEventListener("click", () => {
    socket.emit("unlock_chat");
  });
}

// Listen for server chat status updates
socket.on("chat_status", (data) => {
  if (data.locked) {
    addSystemMessage("🚫 Chat locked by admin");
    chatInput.disabled = true;
    sendBtn.disabled = true;
  } else {
    addSystemMessage("✅ Chat unlocked");
    chatInput.disabled = false;
    sendBtn.disabled = false;
  }
});

// Helper for system messages
function addSystemMessage(text) {
  let div = document.createElement("div");
  div.classList.add("system-msg");
  div.style.textAlign = "center";
  div.style.color = "#666";
  div.style.fontStyle = "italic";
  div.textContent = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}
