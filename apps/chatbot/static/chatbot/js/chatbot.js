/**
 * RAG Chatbot frontend.
 *
 * sendMessage() posts to the live chat endpoint (POST
 * /api/v1/chatbot/messages/), backed by the real RAG pipeline in
 * apps/chatbot/rag.py (falls back to a canned reply only if that pipeline
 * itself errors — see apps/chatbot/services.py::get_bot_reply()).
 *
 * Conversation memory: conversationHistory tracks this tab's exchanges
 * only (no server-side session/DB — see apps.chatbot.rag.
 * _condense_question()'s docstring for why) and is sent with every
 * request so the backend can resolve follow-ups like "what about 2024?".
 * A page refresh starts a fresh conversation by design.
 */
(function () {
  const CHAT_ENDPOINT = '/api/v1/chatbot/messages/';
  // Last N messages sent as history — bounds the condensation prompt's
  // size server-side regardless of how long a session runs (rag.py also
  // caps to the last 4 independently; this is the client-side half of
  // that same bound).
  const MAX_HISTORY_MESSAGES = 6;

  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const messages = document.getElementById('chatMessages');
  const csrfToken = document.getElementById('csrfTokenInput').value;
  const conversationHistory = [];

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  // Formats a raw "sources" entry (e.g. "country_performance:India|2023" or
  // "sql:country_performance:COUNT(DISTINCT country_name)") into a short
  // chip label — sources are plain strings, not objects, so this just
  // trims the table-name prefix and splits the trailing "|year" if present.
  function formatSource(source) {
    const parts = source.split(':');
    const last = parts[parts.length - 1];
    const [key, year] = last.split('|');
    return year ? `${key} (${year})` : key;
  }

  function appendSources(wrapper, sources) {
    if (!sources || sources.length === 0) return;
    const sourcesRow = document.createElement('div');
    sourcesRow.className = 'chat-sources';
    sources.forEach((source) => {
      const chip = document.createElement('span');
      chip.className = 'chat-source-chip';
      chip.textContent = formatSource(source);
      sourcesRow.appendChild(chip);
    });
    wrapper.appendChild(sourcesRow);
  }

  function appendMessage(text, sender, sources) {
    const wrapper = document.createElement('div');
    wrapper.className = `chat-message chat-message-${sender}`;
    // .chat-message is itself a flex row (for left/right alignment), so
    // the bubble and the sources row are grouped in this column container
    // to stack the chips below the bubble instead of beside it.
    const content = document.createElement('div');
    content.className = 'chat-content';
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    if (sender === 'bot') {
      // LLM output only — sanitized before injecting. User input never
      // takes this path (see below), it always stays on textContent.
      bubble.innerHTML = DOMPurify.sanitize(marked.parse(text));
    } else {
      bubble.textContent = text;
    }
    content.appendChild(bubble);
    if (sender === 'bot') appendSources(content, sources);
    wrapper.appendChild(content);
    messages.appendChild(wrapper);
    scrollToBottom();
  }

  function showTypingIndicator() {
    const wrapper = document.createElement('div');
    wrapper.className = 'chat-message chat-message-bot';
    wrapper.id = 'chatTypingIndicator';
    wrapper.innerHTML = '<div class="chat-bubble chat-typing-bubble"><span></span><span></span><span></span></div>';
    messages.appendChild(wrapper);
    scrollToBottom();
  }

  function hideTypingIndicator() {
    const indicator = document.getElementById('chatTypingIndicator');
    if (indicator) indicator.remove();
  }

  async function sendMessage(message) {
    // Single integration point for the RAG/LLM backend — see
    // apps/chatbot/api.py::ChatMessageView and
    // apps/chatbot/services.py::get_bot_reply().
    const response = await fetch(CHAT_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({
        message,
        history: conversationHistory.slice(-MAX_HISTORY_MESSAGES),
      }),
    });
    if (!response.ok) {
      throw new Error(`Chat request failed with status ${response.status}`);
    }
    const data = await response.json();
    return { reply: data.reply, sources: data.sources };
  }

  async function submitMessage(message) {
    appendMessage(message, 'user');
    input.value = '';
    input.focus();

    showTypingIndicator();
    try {
      const { reply, sources } = await sendMessage(message);
      hideTypingIndicator();
      appendMessage(reply, 'bot', sources);
      // Plain text only (not rendered HTML) — this feeds a later
      // condensation prompt server-side, which shouldn't have to fight
      // with markdown syntax to understand what was said.
      conversationHistory.push({ role: 'user', content: message });
      conversationHistory.push({ role: 'assistant', content: reply });
    } catch (error) {
      hideTypingIndicator();
      appendMessage('Sorry, something went wrong. Please try again.', 'bot');
      console.error(error);
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    submitMessage(message);
  });

  // Fixed suggestion chips (see chatbot.html/.css) — clicking one sends it
  // immediately rather than just filling the input, since the whole point
  // is a one-click example, not an extra step.
  const suggestions = document.getElementById('chatSuggestions');
  if (suggestions) {
    suggestions.addEventListener('click', (event) => {
      const chip = event.target.closest('.chat-suggestion-chip');
      if (!chip || chip.disabled) return;
      submitMessage(chip.textContent.trim());
    });
  }
})();
