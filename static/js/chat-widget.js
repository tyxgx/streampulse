/* Floating chat widget — skeleton. Posts to /api/v1/chatbot/messages/,
   same endpoint and {message} / {reply, sources} contract as the full
   chatbot page (apps/chatbot/static/chatbot/js/chatbot.js). */
document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('chatWidgetToggle');
  const panel = document.getElementById('chatWidgetPanel');
  const closeBtn = document.getElementById('chatWidgetClose');
  const form = document.getElementById('chatWidgetForm');
  const input = document.getElementById('chatWidgetInput');
  const messages = document.getElementById('chatWidgetMessages');

  if (!toggle) return;

  const setOpen = (open) => {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  };

  toggle.addEventListener('click', () => setOpen(panel.hidden));
  closeBtn.addEventListener('click', () => setOpen(false));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    appendMessage('user', text);
    input.value = '';

    const res = await fetch('/api/v1/chatbot/messages/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    appendMessage('bot', data.reply, data.sources);
  });

  function appendMessage(role, text, sources) {
    const el = document.createElement('div');
    el.className = `chat-widget-message chat-widget-message-${role}`;
    el.textContent = text;
    if (sources && sources.length) {
      const src = document.createElement('small');
      src.className = 'chat-widget-sources';
      src.textContent = `Sources: ${sources.join(', ')}`;
      el.appendChild(src);
    }
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
  }
});
