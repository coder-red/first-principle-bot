(function () {
  'use strict';

  const state = {
    history: [],
    isWaiting: false,
    abortController: null,
    flashcardMode: false,
  };

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  const messagesEl = $('#messages');
  const inputEl = $('#message-input');
  const sendBtn = $('#send-btn');
  const form = $('#input-form');
  const typingEl = $('#typing-indicator');
  const themeBtn = $('#theme-toggle');
  const modeOptions = $$('.mode-option');
  const toast = $('#error-toast');
  const toastMsg = $('#toast-message');
  const toastClose = $('.toast-close');
  let activeModalClose = null;

  function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('fp_theme', t);
  }

  const savedTheme = localStorage.getItem('fp_theme');
  if (savedTheme) {
    setTheme(savedTheme);
  } else {
    setTheme('dark');
  }

  themeBtn.addEventListener('click', function () {
    setTheme(getTheme() === 'dark' ? 'light' : 'dark');
  });

  let toastTimer = null;

  function showToast(msg) {
    toastMsg.textContent = msg;
    toast.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.classList.add('hidden');
    }, 5000);
  }

  function hideToast() {
    toast.classList.add('hidden');
    clearTimeout(toastTimer);
  }

  toastClose.addEventListener('click', hideToast);

  function scrollToBottom() {
    var main = document.querySelector('main');
    main.scrollTop = main.scrollHeight;
  }

  function addMessage(role, content) {
    var welcome = document.querySelector('.welcome');
    if (welcome) welcome.remove();

    var div = document.createElement('div');
    div.className = 'message ' + role;

    var inner = document.createElement('div');
    inner.className = 'message-content';

    if (role === 'bot') {
      inner.innerHTML = marked.parse(content);
    } else {
      inner.textContent = content;
    }

    div.appendChild(inner);
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
  }

  function updateBotMessage(msgEl, content) {
    var inner = msgEl.querySelector('.message-content');
    if (inner) {
      inner.innerHTML = marked.parse(content);
    }
    scrollToBottom();
  }

  function safeText(value, fallback) {
    return typeof value === 'string' && value.trim() ? value.trim() : fallback;
  }

  function cardImageSvg(card, index) {
    var prompt = safeText(card.imagePrompt, card.title || 'first principles');
    var words = prompt.split(/\s+/).filter(Boolean).slice(0, 5);
    var palette = [
      ['#32d6a0', '#1f6feb', '#f7c948'],
      ['#ff8a5b', '#7353ba', '#2ec4b6'],
      ['#4cc9f0', '#4361ee', '#f72585'],
      ['#a7f3d0', '#f59e0b', '#ef4444'],
      ['#93c5fd', '#14b8a6', '#f97316'],
      ['#c4b5fd', '#22c55e', '#eab308'],
    ][index % 6];
    var label = escapeHtml(words.slice(0, 3).join(' '));
    return [
      '<svg class="flashcard-art" viewBox="0 0 640 280" role="img" aria-label="', escapeHtml(prompt), '">',
      '<rect width="640" height="280" rx="8" fill="', palette[0], '" opacity="0.16"/>',
      '<circle cx="106" cy="86" r="48" fill="', palette[1], '" opacity="0.85"/>',
      '<rect x="178" y="58" width="306" height="26" rx="8" fill="currentColor" opacity="0.16"/>',
      '<rect x="178" y="104" width="226" height="20" rx="8" fill="currentColor" opacity="0.12"/>',
      '<path d="M118 186 C190 116 266 214 338 144 S492 108 560 184" fill="none" stroke="', palette[2], '" stroke-width="12" stroke-linecap="round"/>',
      '<g fill="currentColor" opacity="0.72">',
      '<circle cx="150" cy="188" r="10"/><circle cx="338" cy="144" r="10"/><circle cx="560" cy="184" r="10"/>',
      '</g>',
      '<text x="40" y="246" fill="currentColor" opacity="0.78" font-family="Inter, Arial, sans-serif" font-size="24" font-weight="700">', label, '</text>',
      '</svg>',
    ].join('');
  }

  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function parseFlashcardDeck(content) {
    var cleaned = content.trim().replace(/^```(?:json)?/i, '').replace(/```$/i, '').trim();
    var data = JSON.parse(cleaned);
    if (!data.cards || !Array.isArray(data.cards) || data.cards.length === 0) {
      throw new Error('Flashcard response did not include cards.');
    }
    return {
      topic: safeText(data.topic, 'Flashcards'),
      cards: data.cards.slice(0, 8),
    };
  }

  function flashcardDeckHtml(deck, activeIndex) {
    var card = deck.cards[activeIndex] || {};
    return [
      '<section class="flashcard-deck" aria-label="Flashcard deck">',
      '<div class="flashcard-topline"><span>', escapeHtml(deck.topic), '</span><span>', activeIndex + 1, ' / ', deck.cards.length, '</span></div>',
      '<div class="flashcard-visual">', cardImageSvg(card, activeIndex), '</div>',
      '<div class="flashcard-body">',
      '<h2>', escapeHtml(safeText(card.title, 'First principle')), '</h2>',
      '<p class="flashcard-question">', escapeHtml(safeText(card.question, 'What must be true?')), '</p>',
      '<div class="flashcard-section"><span>Principle</span><p>', escapeHtml(safeText(card.principle, "I don't know.")), '</p></div>',
      '<div class="flashcard-section"><span>Explanation</span><p>', escapeHtml(safeText(card.explanation, "I don't know.")), '</p></div>',
      '<div class="flashcard-takeaway">', escapeHtml(safeText(card.takeaway, 'Keep reducing the idea until only proven pieces remain.')), '</div>',
      '</div>',
      '<div class="flashcard-controls">',
      '<button type="button" class="flashcard-prev" ', activeIndex === 0 ? 'disabled' : '', '>Previous</button>',
      '<div class="flashcard-dots">', deck.cards.map(function (_, i) {
        return '<button type="button" class="flashcard-dot' + (i === activeIndex ? ' active' : '') + '" data-index="' + i + '" aria-label="Go to card ' + (i + 1) + '"></button>';
      }).join(''), '</div>',
      '<button type="button" class="flashcard-next">', activeIndex === deck.cards.length - 1 ? 'Restart' : 'Next', '</button>',
      '</div>',
      '</section>',
    ].join('');
  }

  function openFlashcardModal(deck) {
    if (activeModalClose) activeModalClose();
    var activeIndex = 0;
    var modal = document.createElement('div');
    modal.className = 'flashcard-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    document.body.classList.add('modal-open');
    document.body.appendChild(modal);

    function draw() {
      modal.innerHTML = [
        '<div class="flashcard-backdrop" data-close="true"></div>',
        '<div class="flashcard-dialog">',
        '<button type="button" class="flashcard-close" aria-label="Close flashcards">&times;</button>',
        flashcardDeckHtml(deck, activeIndex),
        '</div>',
      ].join('');
    }

    function close() {
      modal.remove();
      document.body.classList.remove('modal-open');
      document.removeEventListener('keydown', onKeydown);
      activeModalClose = null;
    }

    function onKeydown(e) {
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowRight') move(1);
      if (e.key === 'ArrowLeft') move(-1);
    }

    function move(direction) {
      if (direction < 0) activeIndex = Math.max(0, activeIndex - 1);
      else activeIndex = activeIndex === deck.cards.length - 1 ? 0 : activeIndex + 1;
      draw();
    }

    modal.addEventListener('click', function (e) {
      var dot = e.target.closest('.flashcard-dot');
      if (dot) activeIndex = Number(dot.dataset.index) || 0;
      else if (e.target.closest('.flashcard-prev')) activeIndex = Math.max(0, activeIndex - 1);
      else if (e.target.closest('.flashcard-next')) activeIndex = activeIndex === deck.cards.length - 1 ? 0 : activeIndex + 1;
      else if (e.target.closest('.flashcard-close') || e.target.dataset.close === 'true') return close();
      else return;
      draw();
    });

    activeModalClose = close;
    document.addEventListener('keydown', onKeydown);
    draw();
  }

  function renderFlashcardDeck(msgEl, content) {
    var deck;
    try {
      deck = parseFlashcardDeck(content);
    } catch (err) {
      msgEl.classList.add('flashcard-fallback');
      updateBotMessage(msgEl, content);
      return;
    }

    var firstCard = deck.cards[0] || {};
    var inner = msgEl.querySelector('.message-content');
    msgEl.classList.add('flashcard-mode');
    inner.innerHTML = [
      '<button type="button" class="flashcard-preview">',
      '<span class="flashcard-preview-art">', cardImageSvg(firstCard, 0), '</span>',
      '<span class="flashcard-preview-copy">',
      '<span class="flashcard-preview-label">Flashcard deck</span>',
      '<strong>', escapeHtml(deck.topic), '</strong>',
      '<small>', deck.cards.length, ' cards - click to open</small>',
      '</span>',
      '</button>',
    ].join('');
    inner.querySelector('.flashcard-preview').addEventListener('click', function () {
      openFlashcardModal(deck);
    });
    scrollToBottom();
  }

  function showTyping() {
    typingEl.classList.remove('hidden');
    scrollToBottom();
  }

  function hideTyping() {
    typingEl.classList.add('hidden');
  }

  async function sendMessage(text) {
    if (state.isWaiting) return;
    if (!text.trim()) return;

    state.isWaiting = true;
    sendBtn.disabled = true;
    inputEl.disabled = true;
    hideToast();

    addMessage('user', text);
    state.history.push({ role: 'user', content: text });

    var botMsgEl = addMessage('bot', '_Thinking..._');
    showTyping();

    var fullContent = '';

    try {
      state.abortController = new AbortController();

      var response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          history: state.history,
          mode: state.flashcardMode ? 'flashcard' : 'text',
        }),
        signal: state.abortController.signal,
      });

      if (!response.ok) {
        var errData;
        try { errData = await response.json(); } catch (e) {}
        throw new Error(errData ? errData.detail : 'Request failed with status ' + response.status);
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder();

      while (true) {
        var result = await reader.read();
        if (result.done) break;

        var chunk = decoder.decode(result.value, { stream: true });
        fullContent += chunk;

        if (fullContent.trim()) {
          updateBotMessage(botMsgEl, fullContent);
        }
      }

      if (!fullContent.trim()) {
        updateBotMessage(botMsgEl, '_The bot returned an empty response. Try rephrasing your question._');
      } else if (state.flashcardMode) {
        renderFlashcardDeck(botMsgEl, fullContent);
      }

      state.history.push({ role: 'assistant', content: fullContent || '(empty response)' });
    } catch (err) {
      if (err.name === 'AbortError') {
        updateBotMessage(botMsgEl, '_\u200b_');
        return;
      }

      var errorMsg = err.message || 'Something went wrong.';
      showToast(errorMsg);
      updateBotMessage(botMsgEl, '**Error:** ' + errorMsg + '\n\n_Try again._');
    } finally {
      state.isWaiting = false;
      sendBtn.disabled = false;
      inputEl.disabled = false;
      inputEl.focus();
      hideTyping();
      state.abortController = null;
    }
  }

  function handleInput() {
    var val = inputEl.value.trim();
    sendBtn.disabled = !val || state.isWaiting;

    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  }

  inputEl.addEventListener('input', handleInput);

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (inputEl.value.trim() && !state.isWaiting) {
        var text = inputEl.value.trim();
        inputEl.value = '';
        inputEl.style.height = 'auto';
        sendBtn.disabled = true;
        sendMessage(text);
      }
    }
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
  });

  sendBtn.addEventListener('click', function () {
    var text = inputEl.value.trim();
    if (text && !state.isWaiting) {
      inputEl.value = '';
      inputEl.style.height = 'auto';
      sendBtn.disabled = true;
      sendMessage(text);
    }
  });

  document.addEventListener('click', function (e) {
    var chip = e.target.closest('.suggestion-chip');
    if (chip) {
      var prompt = chip.getAttribute('data-prompt');
      if (prompt) {
        inputEl.value = prompt;
        handleInput();
        sendMessage(prompt);
      }
    }
  });

  function setMode(mode) {
    state.flashcardMode = mode === 'flashcard';
    modeOptions.forEach(function (el) {
      el.classList.toggle('active', el.dataset.mode === mode);
    });
  }

  modeOptions.forEach(function (el) {
    el.addEventListener('click', function () {
      setMode(el.dataset.mode);
    });
  });

  function init() {
    handleInput();
    setMode('text');
  }

  init();
})();
