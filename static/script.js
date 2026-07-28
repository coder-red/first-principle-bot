(function () {
  'use strict';

  const state = {
    history: [],
    isWaiting: false,
    abortController: null,
    flashcardMode: false,
    lastUserMessage: '',
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
  const scrollBtn = $('#scroll-bottom');
  let activeModalClose = null;
  let msgCounter = 0;

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

  function showToast(msg, action) {
    toastMsg.textContent = msg;
    var existingAction = toast.querySelector('.toast-action');
    if (existingAction) existingAction.remove();
    if (action) {
      var btn = document.createElement('button');
      btn.className = 'toast-action';
      btn.textContent = action.label;
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        hideToast();
        if (action.cb) action.cb();
      });
      toast.querySelector('.toast-content').after(btn);
    }
    toast.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.classList.add('hidden');
    }, 8000);
  }

  function hideToast() {
    toast.classList.add('hidden');
    clearTimeout(toastTimer);
  }

  toastClose.addEventListener('click', hideToast);

  function scrollToBottom(smooth) {
    var main = document.querySelector('main');
    main.scrollTo({ top: main.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }

  function isNearBottom() {
    var main = document.querySelector('main');
    return main.scrollHeight - main.scrollTop - main.clientHeight < 120;
  }

  var mainEl = document.querySelector('main');
  if (mainEl) {
    mainEl.addEventListener('scroll', function () {
      if (scrollBtn) {
        scrollBtn.classList.toggle('visible', !isNearBottom());
      }
    });
  }

  if (scrollBtn) {
    scrollBtn.addEventListener('click', function () {
      scrollToBottom(true);
    });
  }

  function timeStr() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function addMessage(role, content) {
    var welcome = document.querySelector('.welcome');
    if (welcome) welcome.remove();

    var div = document.createElement('div');
    div.className = 'message ' + role;
    div.dataset.msgId = ++msgCounter;

    var time = document.createElement('div');
    time.className = 'message-time';
    time.textContent = timeStr();

    var inner = document.createElement('div');
    inner.className = 'message-content';

    if (role === 'bot') {
      inner.innerHTML = marked.parse(content);
    } else {
      inner.textContent = content;
    }

    div.appendChild(time);
    div.appendChild(inner);

    if (role === 'bot') {
      var actions = document.createElement('div');
      actions.className = 'message-actions';

      var copyBtn = document.createElement('button');
      copyBtn.className = 'msg-action copy-btn';
      copyBtn.setAttribute('aria-label', 'Copy response');
      copyBtn.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      copyBtn.addEventListener('click', function () {
        var text = inner.textContent || '';
        navigator.clipboard.writeText(text).then(function () {
          copyBtn.classList.add('copied');
          setTimeout(function () { copyBtn.classList.remove('copied'); }, 1500);
        });
      });

      actions.appendChild(copyBtn);
      div.appendChild(actions);
    }

    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
  }

  function addRegenerateButton() {
    var existing = document.querySelector('.regenerate-bar');
    if (existing) existing.remove();

    var lastBotMsg = document.querySelector('.message.bot:last-of-type');
    if (!lastBotMsg) return;

    var bar = document.createElement('div');
    bar.className = 'regenerate-bar';

    var btn = document.createElement('button');
    btn.className = 'regenerate-btn';
    btn.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Regenerate';
    btn.addEventListener('click', function () {
      if (state.isWaiting || !state.lastUserMessage) return;
      var msgs = document.querySelectorAll('.message.bot:last-of-type');
      msgs.forEach(function (m) {
        var actions = m.querySelector('.message-actions');
        if (actions) actions.remove();
      });
      msgs.forEach(function (m) { m.remove(); });
      var regenBar = document.querySelector('.regenerate-bar');
      if (regenBar) regenBar.remove();
      state.history.pop();
      sendMessage(state.lastUserMessage);
    });

    bar.appendChild(btn);
    lastBotMsg.after(bar);
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
    var total = deck.cards.length;
    var remaining = total - activeIndex - 1;
    var isLast = activeIndex === total - 1;

    var peeks = '';
    for (var i = 1; i <= Math.min(remaining, 3); i++) {
      var nc = deck.cards[activeIndex + i] || {};
      var mt = i === 1 ? -28 : -27;
      peeks += '<div class="peek-card" style="margin-top:' + mt + 'px;z-index:' + (10 - i) + '">' +
        '<span class="peek-title">' + escapeHtml(safeText(nc.title, 'Card ' + (activeIndex + i + 1))) + '</span>' +
        '</div>';
    }

    return [
      '<div class="stack-scene">',
      '<div class="stack-header"><span>', escapeHtml(deck.topic), '</span></div>',
      peeks,
      '<div class="phys-card" data-phys="1">',
      '<div class="phys-card-body">',
      '<div class="phys-marker">', activeIndex + 1, ' / ', total, '</div>',
      '<h2 class="phys-title">', escapeHtml(safeText(card.title, 'First principle')), '</h2>',
      '<p class="phys-question">', escapeHtml(safeText(card.question, 'What must be true?')), '</p>',
      '<div class="phys-section"><span>Principle</span><p>', escapeHtml(safeText(card.principle, "I don't know.")), '</p></div>',
      '<div class="phys-section"><span>Explanation</span><p>', escapeHtml(safeText(card.explanation, "I don't know.")), '</p></div>',
      '<div class="phys-takeaway">', escapeHtml(safeText(card.takeaway, 'Keep reducing the idea until only proven pieces remain.')), '</div>',
      '</div>',
      '</div>',
      '</div>',
      '<div class="stack-controls">',
      '<button type="button" class="s-btn s-prev" ', activeIndex === 0 ? 'disabled' : '', ' aria-label="Previous card">',
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 18 9 12 15 6"/></svg>',
      '</button>',
      '<div class="s-dots">', deck.cards.map(function (_, i) {
        return '<button type="button" class="s-dot' + (i === activeIndex ? ' active' : '') + '" data-i="' + i + '" aria-label="Go to card ' + (i + 1) + '"></button>';
      }).join(''), '</div>',
      '<button type="button" class="s-btn s-next" aria-label="', isLast ? 'Restart' : 'Next card', '">',
      isLast
        ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>'
        : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>',
      '</button>',
      '</div>',
    ].join('');
  }

  function openFlashcardModal(deck) {
    if (activeModalClose) activeModalClose();
    var activeIndex = 0;
    var animating = false;

    var modal = document.createElement('div');
    modal.className = 'flashcard-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    document.body.classList.add('modal-open');
    document.body.appendChild(modal);

    var touchStartX = 0;

    function draw() {
      modal.innerHTML = [
        '<div class="card-backdrop" data-close="true"></div>',
        '<div class="card-dialog" data-dialog="1">',
        '<button type="button" class="card-x" data-close="true" aria-label="Close flashcards">&times;</button>',
        flashcardDeckHtml(deck, activeIndex),
        '</div>',
      ].join('');
      var dialog = modal.querySelector('[data-dialog]');
      if (dialog) {
        dialog.addEventListener('touchstart', function (e) {
          touchStartX = e.changedTouches[0].screenX;
        }, { passive: true });
        dialog.addEventListener('touchend', function (e) {
          var dx = e.changedTouches[0].screenX - touchStartX;
          if (!animating && Math.abs(dx) > 50) {
            move(dx < 0 ? 1 : -1);
          }
        }, { passive: true });
      }
      var card = modal.querySelector('.phys-card');
      if (card) {
        card.addEventListener('click', function (e) {
          if (e.target.closest('.s-btn') || e.target.closest('.s-dot') || e.target.closest('.card-x') || e.target.dataset.close) return;
          if (!animating) move(1);
        });
      }
      requestAnimationFrame(function () {
        var c = modal.querySelector('.phys-card');
        if (c) c.classList.add('in');
      });
    }

    function close() {
      modal.remove();
      document.body.classList.remove('modal-open');
      document.removeEventListener('keydown', onKeydown);
      activeModalClose = null;
    }

    function onKeydown(e) {
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); if (!animating) move(1); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); if (!animating) move(-1); }
    }

    function move(dir) {
      if (animating) return;
      var total = deck.cards.length;
      if (dir < 0) {
        if (activeIndex === 0) return;
        activeIndex -= 1;
      } else {
        if (activeIndex === total - 1) { activeIndex = 0; }
        else { activeIndex += 1; }
      }
      animating = true;
      var cardEl = modal.querySelector('.phys-card');
      if (cardEl) {
        cardEl.classList.remove('in');
        cardEl.style.transition = 'transform 0.2s ease, opacity 0.2s ease';
        cardEl.style.transform = dir < 0 ? 'translateX(-40px)' : 'translateX(40px)';
        cardEl.style.opacity = '0';
      }
      setTimeout(function () {
        draw();
        animating = false;
      }, 220);
    }

    modal.addEventListener('click', function (e) {
      var dot = e.target.closest('.s-dot');
      if (dot) {
        var i = parseInt(dot.dataset.i);
        if (!isNaN(i) && i !== activeIndex && !animating) {
          activeIndex = i;
          animating = true;
          draw();
          animating = false;
        }
        return;
      }
      if (e.target.closest('.s-prev')) { e.preventDefault(); if (!animating) move(-1); return; }
      if (e.target.closest('.s-next')) { e.preventDefault(); if (!animating) move(1); return; }
      if (e.target.closest('.card-x') || e.target.dataset.close === 'true') { close(); }
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

    var inner = msgEl.querySelector('.message-content');
    msgEl.classList.add('flashcard-mode');
    var total = deck.cards.length;
    var firstTitle = escapeHtml(safeText(deck.cards[0] ? deck.cards[0].title : '', ''));
    var topic = escapeHtml(deck.topic);
    var stackHtml = '<div class="preview-stack">';
    for (var i = Math.min(total, 3); i >= 1; i--) {
      var tilt = (i - 1) * 2.5;
      var leftOff = (i - 1) * 4;
      var bottomOff = (i - 1) * 4;
      var z = i + 1;
      var scale = 1 - (total - i) * 0.015;
      stackHtml += '<div class="preview-card pc-' + i + '" style="z-index:' + z + ';transform:rotate(' + (-tilt) + 'deg) translateX(' + leftOff + 'px) translateY(' + (-bottomOff) + 'px) scale(' + scale + ')"></div>';
    }
    stackHtml += '</div>';
    inner.innerHTML = [
      '<button type="button" class="mini-deck">',
      stackHtml,
      '<div class="mini-deck-body">',
      '<span class="mini-label">' + total + ' cards</span>',
      '<strong class="mini-topic">' + topic + '</strong>',
      '<span class="mini-sub">' + firstTitle + '</span>',
      '</div>',
      '</button>',
    ].join('');
    inner.querySelector('.mini-deck').addEventListener('click', function () {
      openFlashcardModal(deck);
    });
    scrollToBottom();
  }

  function showTyping() {
    var label = typingEl.querySelector('.typing-label');
    if (label) {
      label.textContent = state.flashcardMode ? 'Generating flashcards...' : 'Thinking from first principles...';
    }
    typingEl.classList.remove('hidden');
    scrollToBottom();
  }

  function hideTyping() {
    typingEl.classList.add('hidden');
  }

  async function sendMessage(text) {
    if (state.isWaiting) return;
    if (!text.trim()) return;

    state.lastUserMessage = text;
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
        var detail = errData ? errData.detail : 'Request failed with status ' + response.status;
        if (detail && detail.includes('API key')) {
          showToast('API key not configured. Set OPENROUTER_API_KEY in .env', {
            label: 'Fix',
            cb: function () {
              state.history.push({ role: 'assistant', content: '(attempted retry)' });
              sendMessage(state.lastUserMessage);
            },
          });
        } else {
          showToast(detail, {
            label: 'Retry',
            cb: function () { sendMessage(state.lastUserMessage); },
          });
        }
        throw new Error(detail);
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
        var emptyMsg = '_The bot returned an empty response. Try rephrasing your question._';
        updateBotMessage(botMsgEl, emptyMsg);
        showToast('Empty response from model', {
          label: 'Retry',
          cb: function () { sendMessage(state.lastUserMessage); },
        });
      } else if (state.flashcardMode) {
        renderFlashcardDeck(botMsgEl, fullContent);
      }

      state.history.push({ role: 'assistant', content: fullContent || '(empty response)' });
      addRegenerateButton();
    } catch (err) {
      if (err.name === 'AbortError') {
        updateBotMessage(botMsgEl, '_\u200b_');
        return;
      }
      updateBotMessage(botMsgEl, '**Error:** ' + (err.message || 'Something went wrong.') + '\n\n_Try again._');
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
    var badge = document.getElementById('mode-badge');
    if (badge) {
      badge.classList.toggle('hidden', mode !== 'flashcard');
    }
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
