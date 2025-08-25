<script>
  const chatBox = document.getElementById("chat-box");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");
  const bell = document.getElementById("bell");
  const winnersPanel = document.getElementById("winners-panel");
  const winnerList = document.getElementById("winner-list");
  const adminLock = document.getElementById("admin-lock");
  const adminUnlock = document.getElementById("admin-unlock");

  let chatLocked = false;
  let messageSent = false;

  // Toggle winners panel (only if unlocked)
  bell.addEventListener("click", () => {
    if (!messageSent) return; 
    winnersPanel.style.display = winnersPanel.style.display === "flex" ? "none" : "flex";
    chatLocked = true;
    chatInput.disabled = true;
    sendBtn.disabled = true;
  });

  // Function to send message
  function sendMessage() {
    if (chatLocked) return;

    const message = chatInput.value.trim();
    if (message !== "") {
      messageSent = true;
      bell.style.opacity = 1;
      bell.style.pointerEvents = "auto";

      const p = document.createElement("div");
      p.classList.add("msg", "sent");
      p.textContent = message;
      chatBox.appendChild(p);
      chatInput.value = "";
      chatBox.scrollTop = chatBox.scrollHeight;

      // Fake reply
      setTimeout(() => {
        const reply = document.createElement("div");
        reply.classList.add("msg", "received");
        reply.textContent = "Bot: Got it!";
        chatBox.appendChild(reply);
        chatBox.scrollTop = chatBox.scrollHeight;
      }, 800);

      // Add winner example
      if (message.toLowerCase().includes("win")) {
        if (winnerList.innerHTML.includes("No winners yet")) {
          winnerList.innerHTML = "";
        }
        const winner = document.createElement("p");
        winner.textContent = "🏆 " + message;
        winnerList.appendChild(winner);
      }
    }
  }

  // Send button click
  sendBtn.addEventListener("click", sendMessage);

  // ENTER to send
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); 
      sendMessage();
    }
  });

  // Admin controls
  adminLock.addEventListener("click", () => {
    chatLocked = true;
    chatInput.disabled = true;
    sendBtn.disabled = true;
  });

  adminUnlock.addEventListener("click", () => {
    chatLocked = false;
    chatInput.disabled = false;
    sendBtn.disabled = false;
  });
</script>

