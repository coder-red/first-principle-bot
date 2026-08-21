(function () {
  'use strict';

  var state = {
    history: [],
    turns: [],
    trail: [],
    isWaiting: false,
    abortController: null,
    lastQuestion: '',
    lastFocus: null,
    lastDeckEl: null,
    regenBar: null,
  };

  var STORE_KEY = 'fp_thread_v1';
  var MAX_STORED_TURNS = 20;

  // Sits above the server's own ceiling so the server's clearer error wins the
  // race whenever it is the model that stalled.
  var CLIENT_DEADLINE_MS = 150000;
  var SLOW_NOTICE_MS = 20000;

  var $ = function (s) { return document.querySelector(s); };

  var messagesEl = $('#messages');
  var inputEl = $('#message-input');
  var sendBtn = $('#send-btn');
  var form = $('#input-form');
  var typingEl = $('#typing-indicator');
  var themeBtn = $('#theme-toggle');
  var toast = $('#error-toast');
  var toastMsg = $('#toast-message');
  var toastClose = $('.toast-close');
  var scrollBtn = $('#scroll-bottom');
  var mainEl = document.querySelector('main');
  var suggestionsEl = $('#suggestions');
  var shuffleBtn = $('#shuffle-suggestions');
  var railEl = $('#sector-rail');
  var activeModalClose = null;

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  /* ── DOM helpers ───────────────────────────────────────────────────────
     Everything is built as nodes with textContent. Model output never passes
     through innerHTML, so there is no markup-injection surface at all. */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function svg(paths, size, width) {
    var ns = 'http://www.w3.org/2000/svg';
    var s = document.createElementNS(ns, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('width', size || 16);
    s.setAttribute('height', size || 16);
    s.setAttribute('fill', 'none');
    s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', width || 2);
    s.setAttribute('stroke-linecap', 'round');
    s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) {
      var p = document.createElementNS(ns, 'path');
      p.setAttribute('d', d);
      s.appendChild(p);
    });
    return s;
  }

  var ICON = {
    prev: ['M15 18 L9 12 L15 6'],
    next: ['M9 18 L15 12 L9 6'],
    restart: ['M1 4 v6 h6', 'M3.51 15a9 9 0 1 0 2.13-9.36L1 10'],
    copy: ['M9 9 h11 a2 2 0 0 1 2 2 v11 a2 2 0 0 1 -2 2 h-11 a2 2 0 0 1 -2 -2 v-11 a2 2 0 0 1 2 -2 z',
           'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'],
    check: ['M20 6 L9 17 L4 12'],
    deeper: ['M12 5 v14', 'M19 12 l-7 7 -7 -7'],
    send: ['M22 2 L11 13', 'M22 2 L15 22 L11 13 L2 9 Z'],
    stop: ['M7 7 h10 v10 h-10 Z'],
  };

  /* ── theme ─────────────────────────────────────────────────────────── */

  function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('fp_theme', t);
  }

  /* The toggle is gone from the header, but the stored preference is still
     honoured — light is reachable by setting fp_theme, which is what the
     browser suites do. */
  setTheme(localStorage.getItem('fp_theme') || 'dark');
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      setTheme(getTheme() === 'dark' ? 'light' : 'dark');
    });
  }

  /* ── toast ─────────────────────────────────────────────────────────── */

  var toastTimer = null;

  function showToast(msg, action) {
    toastMsg.textContent = msg;
    var existing = toast.querySelector('.toast-action');
    if (existing) existing.remove();
    if (action) {
      var btn = el('button', 'toast-action', action.label);
      btn.type = 'button';
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        hideToast();
        if (action.cb) action.cb();
      });
      toast.querySelector('.toast-content').after(btn);
    }
    toast.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 8000);
  }

  function hideToast() {
    toast.classList.add('hidden');
    clearTimeout(toastTimer);
  }

  toastClose.addEventListener('click', hideToast);

  /* ── access gate ───────────────────────────────────────────────────────
     Only ever seen on an instance that sets APP_ACCESS_TOKEN. The token is
     posted once to /api/access and comes back as an HttpOnly cookie, so it is
     never held in JavaScript and never written to localStorage — a token in
     localStorage survives on a shared machine long after the tab is closed.

     Resolves true once the server has accepted a token, false if the reader
     dismisses the dialog. */

  var gateOpen = false;

  function requestAccess() {
    if (gateOpen) return Promise.resolve(false);
    gateOpen = true;

    return new Promise(function (resolve) {
      var modal = el('div', 'gate-modal');
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', 'Access token required');

      var backdrop = el('div', 'card-backdrop');
      var dialog = el('div', 'gate-dialog');

      var form = el('form', 'gate-form');
      form.appendChild(el('h2', 'gate-title', 'This instance is private'));
      form.appendChild(el('p', 'gate-blurb',
        'Every question here costs the owner a model call, so it asks for a '
        + 'shared access token.'));

      var input = el('input', 'gate-input');
      input.type = 'password';
      input.required = true;
      input.autocomplete = 'current-password';
      input.setAttribute('aria-label', 'Access token');
      input.placeholder = 'Access token';

      var error = el('p', 'gate-error');
      error.setAttribute('role', 'alert');
      error.hidden = true;

      var submit = el('button', 'gate-submit', 'Unlock');
      submit.type = 'submit';

      var cancel = el('button', 'gate-cancel', 'Not now');
      cancel.type = 'button';

      var actions = el('div', 'gate-actions');
      actions.append(cancel, submit);
      form.append(input, error, actions);
      dialog.appendChild(form);
      modal.append(backdrop, dialog);

      function close(granted) {
        document.removeEventListener('keydown', onKeydown, true);
        modal.remove();
        document.body.classList.remove('modal-open');
        gateOpen = false;
        resolve(granted);
      }

      function onKeydown(e) {
        if (e.key === 'Escape') { e.preventDefault(); close(false); return; }
        if (e.key !== 'Tab') return;
        // Small enough to trap by hand: input, cancel, submit.
        var items = [input, cancel, submit];
        var first = items[0];
        var last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault(); first.focus();
        } else if (!dialog.contains(document.activeElement)) {
          e.preventDefault(); first.focus();
        }
      }

      function fail(message) {
        error.textContent = message;
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = 'Unlock';
        input.select();
      }

      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var value = input.value.trim();
        if (!value) return;

        submit.disabled = true;
        submit.textContent = 'Checking...';
        error.hidden = true;

        fetch('/api/access', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: value }),
        }).then(function (res) {
          if (res.ok) { close(true); return; }
          // 429 means the guess budget is spent; saying so beats "not valid",
          // which would read as a wrong token and invite more guessing.
          fail(res.status === 429
            ? 'Too many attempts. Wait a few minutes and try again.'
            : 'That token was not accepted.');
        }).catch(function () {
          fail('Could not reach the server.');
        });
      });

      cancel.addEventListener('click', function () { close(false); });
      backdrop.addEventListener('click', function () { close(false); });
      document.addEventListener('keydown', onKeydown, true);

      document.body.classList.add('modal-open');
      document.body.appendChild(modal);
      input.focus();
    });
  }

  /* ── scrolling ─────────────────────────────────────────────────────── */

  function scrollToBottom(smooth) {
    mainEl.scrollTo({ top: mainEl.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }

  function isNearBottom() {
    return mainEl.scrollHeight - mainEl.scrollTop - mainEl.clientHeight < 120;
  }

  mainEl.addEventListener('scroll', function () {
    scrollBtn.classList.toggle('visible', !isNearBottom());
  });
  scrollBtn.addEventListener('click', function () { scrollToBottom(true); });

  /* ── suggestions ───────────────────────────────────────────────────── */

  /* Questions where the conventional answer is itself a convention —
     decomposing them actually pays off, which is the point of the app. The
     hook names the belief the question takes apart, which is the whole reason
     one of these is worth clicking over a search box.
     No emoji: newer codepoints render as tofu on Windows, and a precise tool
     reads better without them. */
  var PICKED = [
    { question: 'Why do planes actually stay up?', hook: 'the equal-transit myth' },
    { question: 'Why does a mirror flip left to right but not up and down?', hook: 'it does neither' },
    { question: 'What is money, really?', hook: 'money as a substance' },
    { question: 'What is fire?', hook: 'fire as a thing' },
    { question: 'Why can nothing travel faster than light?', hook: 'light as a speed limit' },
    { question: 'Why do we have to sleep?', hook: 'sleep as rest' },
    { question: 'Why does ice float when almost every other solid sinks?', hook: 'solids sink' },
    { question: 'Why does music sound good?', hook: 'beauty as taste' },
    { question: 'Why can you not tickle yourself?', hook: 'touch as sensation' },
    { question: 'What is actually moving when electricity flows?', hook: 'current as flow' },
    { question: 'How do we know the Earth is round without leaving it?', hook: 'seeing is proof' },
    { question: 'Why is glass transparent?', hook: 'solids block light' },
    { question: 'How does a magnet pull on something it never touches?', hook: 'action at a distance' },
    { question: 'Why is the sky blue?', hook: 'the sky as an object' },
    { question: 'Why can we not remember being a baby?', hook: 'memory as recording' },
    { question: 'What is time?', hook: 'time as a river' },
    { question: 'How does anaesthesia switch consciousness off?', hook: 'sleep and anaesthesia' },
    { question: 'Why is the sea salty but rivers are not?', hook: 'rivers as fresh' },
  ];

  /* One line-art glyph per sector, keyed by slug. Kept client-side because it
     is presentation; the server owns the sector list itself. */
  var SECTOR_GLYPHS = {
    picked:     'M12 3l2.1 5.4L20 10l-5.9 1.6L12 17l-2.1-5.4L4 10l5.9-1.6z',
    career:     'M3 8h18v11H3zM8 8V6a2 2 0 012-2h4a2 2 0 012 2v2M3 13h18',
    health:     'M3 12h4l2-5 3 10 2-5h7',
    football:   'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7l4.2 3-1.6 5H9.4l-1.6-5z',
    history:    'M12 3a9 9 0 109 9M12 7v5l3 2M12 3l3 3-3 3',
    money:      'M12 3v18M8 7h6a3 3 0 010 6H9a3 3 0 000 6h7',
    mind:       'M9 21v-3a5 5 0 01-2-4V9a5 5 0 0110 0v1h2l-2 3.5V17a2 2 0 01-2 2h-2v2',
    physics:    'M12 10a2 2 0 100 4 2 2 0 000-4M4.5 8.5c5-3 10-3 15 0M4.5 15.5c5 3 10 3 15 0',
    technology: 'M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4',
    everyday:   'M3 11l9-7 9 7v9a1 1 0 01-1 1H4a1 1 0 01-1-1zM9 21v-6h6v6',
  };

  var PICKED_SECTOR = { slug: 'picked', label: 'Picked', blurb: 'hand-written' };

  var explore = { slug: 'picked', cursor: 0, busy: false };

  function svgGlyph(slug) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '15');
    svg.setAttribute('height', '15');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.6');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', SECTOR_GLYPHS[slug] || SECTOR_GLYPHS.picked);
    svg.appendChild(path);
    return svg;
  }

  function renderRail(sectors) {
    if (!railEl) return;
    railEl.replaceChildren();
    sectors.forEach(function (sector) {
      var pill = el('button', 'sector-pill');
      pill.type = 'button';
      pill.setAttribute('role', 'tab');
      pill.dataset.slug = sector.slug;
      pill.title = sector.blurb || sector.label;
      pill.appendChild(svgGlyph(sector.slug));
      pill.appendChild(el('span', '', sector.label));
      railEl.appendChild(pill);
    });
    markActivePill();
  }

  function markActivePill() {
    if (!railEl) return;
    Array.prototype.forEach.call(railEl.children, function (pill) {
      var on = pill.dataset.slug === explore.slug;
      pill.classList.toggle('active', on);
      pill.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }

  function renderQuestions(items) {
    if (!suggestionsEl) return;
    suggestionsEl.replaceChildren();
    items.forEach(function (item) {
      var card = el('button', 'suggestion-chip');
      card.type = 'button';
      card.dataset.prompt = item.question;
      var body = el('span', 'chip-body');
      if (item.hook) body.appendChild(el('span', 'chip-hook', item.hook));
      body.appendChild(el('span', 'chip-text', item.question));
      card.appendChild(body);
      card.appendChild(el('span', 'chip-arrow', '→'));
      suggestionsEl.appendChild(card);
    });
    suggestionsEl.classList.remove('reshuffled');
    void suggestionsEl.offsetWidth;
    suggestionsEl.classList.add('reshuffled');
  }

  /* ── library ────────────────────────────────────────────────────────────
     Reviewed decks, served from disk: instant, free, and the only decks in
     the app whose content a human has actually read. The index is fetched
     once and cached; a failure just means an empty shelf. */
  var libraryIndex = null;
  function loadLibraryIndex() {
    if (libraryIndex) return Promise.resolve(libraryIndex);
    return fetch('/api/library')
      .then(function (r) { return r.ok ? r.json() : { decks: [] }; })
      .then(function (data) { libraryIndex = data.decks || []; return libraryIndex; })
      .catch(function () { return []; });
  }

  function renderShelf(slug) {
    var host = $('#library-shelf');
    if (!host) {
      if (!suggestionsEl) return;
      host = el('div', null);
      host.id = 'library-shelf';
      suggestionsEl.parentNode.insertBefore(host, suggestionsEl);
    }
    host.replaceChildren();
    loadLibraryIndex().then(function (decks) {
      if (explore.slug !== slug) return;  // a later pill won the race
      decks.filter(function (d) { return d.sector === slug; })
        .forEach(function (d) {
          var chip = el('button', 'suggestion-chip library-chip');
          chip.type = 'button';
          var body = el('span', 'chip-body');
          body.appendChild(el('span', 'chip-hook', '✓ reviewed'));
          body.appendChild(el('span', 'chip-text', d.question));
          chip.appendChild(body);
          chip.appendChild(el('span', 'chip-arrow', '→'));
          chip.addEventListener('click', function () { openLibraryDeck(d.slug); });
          host.appendChild(chip);
        });
    });
  }

  function openLibraryDeck(slug) {
    fetch('/api/library/' + encodeURIComponent(slug))
      .then(function (r) {
        if (!r.ok) throw new Error('library deck missing');
        return r.json();
      })
      .then(function (deck) {
        addUserMessage(deck.question);
        var block = addPendingDeck();
        renderDeck(block, deck);
        state.history.push({ role: 'user', content: deck.question });
        state.history.push({ role: 'assistant', content: deckSummary(deck) });
        state.turns.push({ q: deck.question, deck: deck, trail: [], focus: null });
        saveThread();
        if (newThreadBtn) newThreadBtn.hidden = false;
      })
      .catch(function () {
        showToast('Could not load that deck.');
      });
  }

  function renderSkeletons(count) {
    if (!suggestionsEl) return;
    suggestionsEl.replaceChildren();
    for (var i = 0; i < count; i++) {
      suggestionsEl.appendChild(el('div', 'chip-skeleton'));
    }
  }

  function renderExploreNote(message) {
    if (!suggestionsEl) return;
    suggestionsEl.replaceChildren();
    suggestionsEl.appendChild(el('p', 'explore-note', message));
  }

  function setShuffleLabel(text) {
    var label = $('#shuffle-label');
    if (label) label.textContent = text;
  }

  function showPicked() {
    var picks = [];
    for (var i = 0; i < 4; i++) {
      picks.push(PICKED[(explore.cursor + i) % PICKED.length]);
    }
    explore.cursor = (explore.cursor + 4) % PICKED.length;
    renderQuestions(picks);
    setShuffleLabel('Show me others');
  }

  /* Generated questions cost a model call, so the button says so rather than
     implying the same instant reshuffle the hand-written list gives. */
  function loadSector(slug) {
    if (explore.busy) return;
    explore.slug = slug;
    markActivePill();
    renderShelf(slug);

    if (slug === 'picked') { showPicked(); return; }

    explore.busy = true;
    if (shuffleBtn) shuffleBtn.disabled = true;
    setShuffleLabel('Writing questions...');
    renderSkeletons(6);

    fetch('/api/explore/' + encodeURIComponent(slug))
      .then(function (r) {
        if (r.status === 401) {
          // A gated instance, and this is the first request the page makes —
          // so the gate is what a visitor sees on load, which is correct.
          var gateErr = new Error('sector 401');
          gateErr.gated = true;
          throw gateErr;
        }
        if (!r.ok) throw new Error('sector ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (explore.slug !== slug) return;  // a later pill won the race
        var items = (data && data.questions) || [];
        if (!items.length) {
          renderExploreNote('No questions came back for this one. Try another sector, or just ask.');
        } else {
          renderQuestions(items);
        }
      })
      .catch(function (err) {
        if (explore.slug !== slug) return;
        if (err && err.gated) {
          renderExploreNote('Unlock this instance to browse questions.');
          requestAccess().then(function (ok) { if (ok) loadSector(slug); });
          return;
        }
        renderExploreNote('Could not reach the question writer. Try again, or just ask.');
      })
      .finally(function () {
        explore.busy = false;
        if (shuffleBtn) shuffleBtn.disabled = false;
        setShuffleLabel('More questions');
      });
  }

  function renderSuggestions() { loadSector(explore.slug); }

  function initExplore() {
    if (!railEl) return;
    fetch('/api/explore/sectors')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderRail([PICKED_SECTOR].concat((data && data.sectors) || []));
      })
      .catch(function () { renderRail([PICKED_SECTOR]); });
    showPicked();
  }

  if (railEl) {
    railEl.addEventListener('click', function (e) {
      var pill = e.target.closest('.sector-pill');
      if (pill && pill.dataset.slug) loadSector(pill.dataset.slug);
    });
  }

  if (shuffleBtn) {
    shuffleBtn.addEventListener('click', function () { loadSector(explore.slug); });
  }

  /* ── epistemic tags ────────────────────────────────────────────────── */

  /* `plain` is the quiz-facing wording: the tag words are this app's own
     vocabulary, and a first-time reader meeting five of them as bare answer
     options learns nothing except that they are confused. The sentence does
     the teaching; the tag word rides along as an accent until it is familiar. */
  var TAG_META = {
    ATOMIC:     { glyph: '◆', label: 'Atomic',     hint: 'Irreducible — cannot be broken down further',
                  plain: 'A law of nature or logic — could not be otherwise' },
    VERIFIED:   { glyph: '✓', label: 'Verified',   hint: 'Empirically confirmed, but could be otherwise',
                  plain: 'Proven by evidence — but could have been different' },
    CONVENTION: { glyph: '≈', label: 'Convention', hint: 'Widely accepted, not proven',
                  plain: 'True mainly because everyone agrees it is' },
    ASSUMPTION: { glyph: '○', label: 'Assumption', hint: 'Taken for granted',
                  plain: 'Taken for granted — nobody has proven it' },
    UNKNOWN:    { glyph: '?', label: 'Unknown',    hint: 'Not known — reasoning stops here',
                  plain: 'Nobody actually knows' },
  };

  var PHASE_LABEL = {
    question: 'Starting point',
    descent:  'Descent',
    bedrock:  'Bedrock',
    rebuild:  'Rebuild',
    insight:  'Insight',
  };

  function phaseText(card) {
    var base = PHASE_LABEL[card.phase] || 'Step';
    if (card.phase === 'question' || card.phase === 'insight') return base;
    return base + ' · Level ' + card.level;
  }

  function buildTagChip(tag, small) {
    var meta = TAG_META[tag];
    if (!meta) return null;
    var chip = el('span', 'tag-chip tag-' + tag.toLowerCase() + (small ? ' tag-sm' : ''));
    chip.appendChild(el('span', 'tag-glyph', meta.glyph));
    chip.appendChild(el('span', 'tag-label', meta.label));
    chip.title = meta.hint;
    return chip;
  }

  function buildSection(label, className) {
    var section = el('section', 'card-section ' + (className || ''));
    section.appendChild(el('h3', 'card-section-label', label));
    return section;
  }

  /* ── the chain ladder ──────────────────────────────────────────────────
     Each rung is one level of decomposition and is itself a drill-down
     target: the chain is explorable, not a fixed picture. */
  function buildChain(chain, tag, onDrill) {
    var section = buildSection('The chain', 'chain-section');
    var list = el('ol', 'chain');
    chain.forEach(function (step, i) {
      var isLast = i === chain.length - 1;
      var item = el('li', 'chain-step' + (isLast ? ' is-last' : ''));
      item.style.setProperty('--rung', i);
      item.appendChild(el('span', 'chain-node', isLast && tag === 'ATOMIC' ? '◆' : ''));

      if (onDrill) {
        var btn = el('button', 'chain-text chain-drill');
        btn.type = 'button';
        btn.appendChild(el('span', null, step));
        btn.appendChild(el('span', 'chain-drill-hint', 'go deeper'));
        btn.title = 'Decompose: ' + step;
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          onDrill({ title: step, principle: step, chain: chain.slice(0, i + 1) });
        });
        item.appendChild(btn);
      } else {
        item.appendChild(el('span', 'chain-text', step));
      }
      list.appendChild(item);
    });
    section.appendChild(list);
    return section;
  }

  function buildDiscarded(discarded) {
    var section = buildSection('Discarded here', 'discarded-section');
    var list = el('ul', 'discarded');
    discarded.forEach(function (item) {
      var li = el('li', 'discarded-item');
      li.appendChild(el('span', 'discarded-mark', '✕'));
      li.appendChild(el('span', null, item));
      list.appendChild(li);
    });
    section.appendChild(list);
    return section;
  }

  function buildCardFace(deck, index, onDrill) {
    var card = deck.cards[index];
    var frag = document.createDocumentFragment();

    var head = el('div', 'card-head');
    head.appendChild(el('span', 'card-phase', phaseText(card)));
    var chip = buildTagChip(card.tag);
    if (chip) head.appendChild(chip);
    frag.appendChild(head);

    frag.appendChild(el('h2', 'card-title', card.title));
    frag.appendChild(el('p', 'card-question', card.question));

    if (card.principle) {
      var principle = buildSection('Principle', 'principle-section');
      principle.appendChild(el('p', 'card-principle', card.principle));
      frag.appendChild(principle);
    }

    if (card.chain && card.chain.length) {
      frag.appendChild(buildChain(card.chain, card.tag, onDrill));
    }

    if (card.discarded && card.discarded.length) {
      frag.appendChild(buildDiscarded(card.discarded));
    }

    if (card.explanation) {
      var why = buildSection('Why', 'why-section');
      why.appendChild(el('p', 'card-explanation', card.explanation));
      frag.appendChild(why);
    }

    if (card.takeaway) {
      frag.appendChild(el('p', 'card-takeaway', card.takeaway));
    }

    if (onDrill && card.principle && card.tag !== 'ATOMIC') {
      var deeper = el('button', 'go-deeper');
      deeper.type = 'button';
      deeper.appendChild(svg(ICON.deeper, 14));
      deeper.appendChild(el('span', null, 'Decompose this further'));
      deeper.addEventListener('click', function (e) {
        e.stopPropagation();
        onDrill({ title: card.title, principle: card.principle, chain: card.chain || [] });
      });
      frag.appendChild(deeper);
    }

    return frag;
  }

  /* ── prose view ────────────────────────────────────────────────────────
     Built from the SAME deck JSON as the cards — one model call, two
     renderings. No second prompt and no markdown wall to style. */

  function proseBlock(card, onDrill) {
    var block = el('article', 'prose-step phase-' + card.phase);

    var head = el('div', 'prose-step-head');
    head.appendChild(el('span', 'prose-step-phase', phaseText(card)));
    var chip = buildTagChip(card.tag, true);
    if (chip) head.appendChild(chip);
    block.appendChild(head);

    block.appendChild(el('h3', 'prose-step-title', card.title));
    if (card.principle) block.appendChild(el('p', 'prose-principle', card.principle));
    if (card.explanation) block.appendChild(el('p', 'prose-explanation', card.explanation));

    if (card.discarded && card.discarded.length) {
      var list = el('ul', 'discarded prose-discarded');
      card.discarded.forEach(function (d) {
        var li = el('li', 'discarded-item');
        li.appendChild(el('span', 'discarded-mark', '✕'));
        li.appendChild(el('span', null, d));
        list.appendChild(li);
      });
      block.appendChild(list);
    }
    return block;
  }

  function buildProse(deck, onDrill) {
    var frag = document.createDocumentFragment();

    var intro = el('section', 'prose-intro');
    intro.appendChild(el('h3', 'prose-label', 'The question'));
    intro.appendChild(el('p', 'prose-question', deck.question));
    frag.appendChild(intro);

    // The deepest card carries the full path down, so the ladder is shown once
    // here rather than repeated under every step.
    var deepest = deck.cards.reduce(function (best, c) {
      return (c.chain && c.chain.length > (best && best.chain ? best.chain.length : 0)) ? c : best;
    }, null);

    if (deepest && deepest.chain.length) {
      var chainWrap = el('section', 'prose-chain-wrap');
      chainWrap.appendChild(buildChain(deepest.chain, deepest.tag, onDrill));
      frag.appendChild(chainWrap);
    }

    var steps = el('section', 'prose-steps');
    deck.cards.forEach(function (card) {
      steps.appendChild(proseBlock(card, onDrill));
    });
    frag.appendChild(steps);

    return frag;
  }

  /* ── quiz ──────────────────────────────────────────────────────────────
     Tests the method, not recall. Asking "what did card 3 say?" would test
     memorisation, which is the mode this whole app argues against. Telling a
     physical necessity from a human convention is the one transferable skill
     in the deck, so that is what gets asked.

     Every question is derived from the deck JSON — no extra model call. */

  var QUIZ_TAGS = ['ATOMIC', 'VERIFIED', 'CONVENTION', 'ASSUMPTION', 'UNKNOWN'];
  var MAX_QUIZ_QUESTIONS = 5;

  function shuffled(list) {
    var out = list.slice();
    for (var i = out.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = out[i]; out[i] = out[j]; out[j] = t;
    }
    return out;
  }

  function quizQuestions(deck) {
    var usable = deck.cards.filter(function (c) { return c.tag && c.principle; });
    // Bedrock first — it is the deck's point — then spread across the rest.
    var bedrock = usable.filter(function (c) { return c.phase === 'bedrock'; });
    var rest = shuffled(usable.filter(function (c) { return c.phase !== 'bedrock'; }));
    return bedrock.concat(rest).slice(0, MAX_QUIZ_QUESTIONS).map(function (card) {
      var distractors = shuffled(QUIZ_TAGS.filter(function (t) { return t !== card.tag; })).slice(0, 3);
      return { card: card, options: shuffled([card.tag].concat(distractors)) };
    });
  }

  function buildQuiz(deck, presetQuestions) {
    var wrap = el('div', 'quiz');

    if (!deck.verified) {
      // Quizzing an unsound chain would teach the wrong thing outright.
      var blocked = el('div', 'quiz-blocked');
      blocked.appendChild(el('p', 'quiz-blocked-head', 'Not available for this deck'));
      blocked.appendChild(el('p', 'quiz-blocked-body',
        'This chain did not pass validation, so its tags cannot be trusted as '
        + 'answers. Testing yourself against them would teach the wrong thing.'));
      wrap.appendChild(blocked);
      return wrap;
    }

    var questions = presetQuestions || quizQuestions(deck);
    if (questions.length < 2) {
      wrap.appendChild(el('p', 'quiz-blocked-body',
        'This deck has too few tagged claims to test.'));
      return wrap;
    }

    var index = 0;
    var matched = 0;
    var body = el('div', 'quiz-body');
    wrap.appendChild(body);

    function renderSummary() {
      body.replaceChildren();
      var sum = el('div', 'quiz-summary');
      sum.appendChild(el('p', 'quiz-summary-count',
        'You matched the deck on ' + matched + ' of ' + questions.length + ' claims.'));
      // Deliberately "matched", not "scored": these tags are the model's
      // judgement, not ground truth, and disagreeing can be the right call.
      sum.appendChild(el('p', 'quiz-summary-note',
        'These are the deck’s own judgements, not settled fact. Where you '
        + 'disagreed, the interesting question is which of you is right.'));
      var again = el('button', 'quiz-again');
      again.type = 'button';
      again.textContent = 'Go again';
      again.addEventListener('click', function () {
        questions = quizQuestions(deck);
        index = 0; matched = 0; renderQuestion();
      });
      sum.appendChild(again);
      body.appendChild(sum);
    }

    function renderQuestion() {
      if (index >= questions.length) return renderSummary();

      var q = questions[index];
      body.replaceChildren();

      body.appendChild(el('p', 'quiz-progress',
        'Claim ' + (index + 1) + ' of ' + questions.length));
      body.appendChild(el('blockquote', 'quiz-claim', q.card.principle));
      body.appendChild(el('p', 'quiz-ask', 'How solid is this claim?'));
      if (index === 0) {
        body.appendChild(el('p', 'quiz-intro',
          'The deck rated every claim by how well it is known. Guess its '
          + 'rating — and if you disagree with the deck, you might be the '
          + 'one who is right.'));
      }

      var opts = el('div', 'quiz-options');
      var answered = false;

      q.options.forEach(function (tag) {
        var meta = TAG_META[tag];
        var btn = el('button', 'quiz-option');
        btn.type = 'button';
        btn.appendChild(el('span', 'quiz-option-glyph tag-text-' + tag.toLowerCase(), meta.glyph));
        var optText = el('span', 'quiz-option-text');
        optText.appendChild(el('span', 'quiz-option-label', meta.plain));
        optText.appendChild(el('span', 'quiz-option-tagword', meta.label));
        btn.appendChild(optText);
        btn.addEventListener('click', function () {
          if (answered) return;
          answered = true;
          var right = tag === q.card.tag;
          if (right) matched++;
          reviewRecord(q.card, deck, right);

          opts.querySelectorAll('.quiz-option').forEach(function (b) { b.disabled = true; });
          btn.classList.add(right ? 'is-right' : 'is-wrong');
          if (!right) {
            var correct = opts.querySelector('[data-correct]');
            if (correct) correct.classList.add('is-right');
          }
          body.appendChild(reveal(q, right));
        });
        if (tag === q.card.tag) btn.dataset.correct = 'true';
        opts.appendChild(btn);
      });

      body.appendChild(opts);
    }

    function reveal(q, right) {
      var meta = TAG_META[q.card.tag];
      var box = el('div', 'quiz-reveal');

      var head = el('p', 'quiz-verdict ' + (right ? 'is-right' : 'is-wrong'));
      head.appendChild(el('span', 'quiz-verdict-glyph', right ? '✓' : '✕'));
      head.appendChild(el('span', null,
        right ? 'That is how the deck tags it' : 'The deck tags it ' + meta.label));
      box.appendChild(head);

      box.appendChild(el('p', 'quiz-meaning', meta.label + ' — ' + meta.hint));
      if (q.card.explanation) box.appendChild(el('p', 'quiz-why', q.card.explanation));

      var next = el('button', 'quiz-next');
      next.type = 'button';
      next.textContent = index + 1 >= questions.length ? 'See how you did' : 'Next claim';
      next.addEventListener('click', function () { index++; renderQuestion(); });
      box.appendChild(next);
      return box;
    }

    renderQuestion();
    return wrap;
  }

  /* ── spaced review ─────────────────────────────────────────────────────
     Quiz answers feed a review queue: SM-2 with the numbers filed off. A
     fixed ladder of intervals, matched climbs one rung, missed falls to the
     bottom. It is honest about being a ladder, not science — the point is a
     reason to come back tomorrow, not a memory model.
     Progress is per-browser by design: no accounts, nothing leaves the
     machine. */
  var REVIEW_KEY = 'fp_review';
  var REVIEW_CAP = 500;
  var REVIEW_ROUND = 12;
  var DAY_MS = 24 * 60 * 60 * 1000;
  var REVIEW_LADDER = [1, 3, 7, 16, 35];  /* days */

  function reviewLoad() {
    try {
      var raw = JSON.parse(localStorage.getItem(REVIEW_KEY) || 'null');
      if (raw && raw.v === 1 && Array.isArray(raw.claims)) return raw.claims;
    } catch (e) {}
    return [];
  }

  function reviewSave(claims) {
    if (claims.length > REVIEW_CAP) {
      claims = claims.slice().sort(function (a, b) { return b.ts - a.ts; })
        .slice(0, REVIEW_CAP);
    }
    try {
      localStorage.setItem(REVIEW_KEY, JSON.stringify({ v: 1, claims: claims }));
    } catch (e) {}
  }

  function reviewRecord(card, deck, matched) {
    if (!card || !card.principle || !card.tag) return;
    var now = Date.now();
    var slug = (card && card._reviewSlug) ||
               (deck && deck.meta && deck.meta.slug) || '';
    var id = slug + '|' + card.principle;
    var claims = reviewLoad();
    var entry = null;
    for (var i = 0; i < claims.length; i++) {
      if (claims[i].id === id) { entry = claims[i]; break; }
    }
    if (!entry) {
      entry = { id: id, claim: card.principle, tag: card.tag, deckSlug: slug,
                deckTopic: (deck && deck.topic) || '', rung: 0, due: 0, ts: 0 };
      claims.push(entry);
    }
    entry.rung = matched ? Math.min(entry.rung + 1, REVIEW_LADDER.length - 1) : 0;
    /* A fresh claim starts at the bottom rung either way; only repeats climb. */
    if (entry.ts === 0) entry.rung = 0;
    entry.due = now + REVIEW_LADDER[entry.rung] * DAY_MS;
    entry.ts = now;
    reviewSave(claims);
  }

  function reviewDue() {
    var now = Date.now();
    return reviewLoad().filter(function (c) { return c.due <= now; });
  }

  function renderReviewBanner() {
    var existing = $('#review-banner');
    if (existing) existing.remove();
    var due = reviewDue();
    if (!due.length) return;
    var bar = el('div', 'review-banner');
    bar.id = 'review-banner';
    bar.appendChild(el('span', 'review-banner-text',
      due.length + ' claim' + (due.length === 1 ? '' : 's') + ' due for review'));
    var go = el('button', 'review-banner-go', 'Review now');
    go.type = 'button';
    go.addEventListener('click', startReview);
    bar.appendChild(go);
    messagesEl.parentNode.insertBefore(bar, messagesEl);
  }

  function startReview() {
    var due = shuffled(reviewDue()).slice(0, REVIEW_ROUND);
    if (!due.length) return;
    var pseudoDeck = { topic: 'Review', verified: true, cards: [], meta: null };
    var questions = due.map(function (c) {
      var card = { principle: c.claim, tag: c.tag, explanation: '',
                   title: c.deckTopic, phase: 'descent', level: 1,
                   _reviewSlug: c.deckSlug };
      var distractors = shuffled(QUIZ_TAGS.filter(function (t) { return t !== c.tag; })).slice(0, 3);
      return { card: card, options: shuffled([c.tag].concat(distractors)) };
    });
    var welcome = document.querySelector('.welcome');
    if (welcome) welcome.remove();
    var block = el('div', 'answer review-round');
    block.appendChild(el('p', 'review-round-head', 'Review — claims from your past decks'));
    block.appendChild(buildQuiz(pseudoDeck, questions));
    messagesEl.appendChild(block);
    renderReviewBanner();  /* re-render; the round may empty the queue */
    scrollToBottom(true);
  }

  function deckToText(deck) {
    var lines = [deck.topic, deck.question, ''];
    deck.cards.forEach(function (c) {
      lines.push('— ' + phaseText(c) + (c.tag ? '  [' + c.tag + ']' : ''));
      lines.push(c.title);
      if (c.principle) lines.push(c.principle);
      if (c.chain && c.chain.length) lines.push('Chain: ' + c.chain.join(' -> '));
      if (c.discarded && c.discarded.length) lines.push('Discarded: ' + c.discarded.join('; '));
      if (c.explanation) lines.push(c.explanation);
      lines.push('');
    });
    return lines.join('\n');
  }

  /* ── deck modal ────────────────────────────────────────────────────── */

  function openDeck(deck, startIndex) {
    if (activeModalClose) activeModalClose();

    var total = deck.cards.length;
    var index = Math.min(Math.max(parseInt(startIndex, 10) || 0, 0), total - 1);
    var maxLevel = deck.cards.reduce(function (m, c) { return Math.max(m, c.level); }, 1) || 1;
    var busy = false;
    var view = 'cards';

    function drill(focus) {
      close();
      sendMessage('Decompose this further: ' + focus.principle, focus);
    }

    var modal = el('div', 'flashcard-modal');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Deck: ' + deck.topic);

    var backdrop = el('div', 'card-backdrop');
    backdrop.dataset.close = 'true';

    var dialog = el('div', 'card-dialog');

    var closeBtn = el('button', 'card-x');
    closeBtn.type = 'button';
    closeBtn.dataset.close = 'true';
    closeBtn.setAttribute('aria-label', 'Close deck');
    closeBtn.textContent = '×';

    var header = el('header', 'deck-header');
    header.appendChild(el('p', 'deck-topic', deck.topic));
    if (deck.question) header.appendChild(el('p', 'deck-question', deck.question));

    /* view switch */
    var tabs = el('div', 'view-tabs');
    tabs.setAttribute('role', 'tablist');
    var cardsTab = el('button', 'view-tab active', 'Cards');
    var proseTab = el('button', 'view-tab', 'Full reasoning');
    var quizTab = el('button', 'view-tab', 'Test yourself');
    [cardsTab, proseTab, quizTab].forEach(function (t) {
      t.type = 'button';
      t.setAttribute('role', 'tab');
    });
    if (!deck.verified) {
      quizTab.classList.add('is-muted');
      quizTab.title = 'This chain did not pass validation';
    }
    tabs.append(cardsTab, proseTab, quizTab);

    /* cards view */
    var stack = el('div', 'stack');
    var peek2 = el('div', 'peek peek-2');
    var peek1 = el('div', 'peek peek-1');
    var face = el('article', 'card-face');
    stack.append(peek2, peek1, face);

    // A live region on the card face re-read every field on every move. A
    // short status line announces the move; the card itself stays readable
    // on demand.
    var status = el('p', 'sr-only');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');

    var controls = el('div', 'deck-controls');
    var prevBtn = el('button', 'nav-btn nav-prev');
    prevBtn.type = 'button';
    prevBtn.setAttribute('aria-label', 'Previous card');
    prevBtn.appendChild(svg(ICON.prev, 16, 2.5));

    var rail = el('div', 'depth-rail');
    rail.setAttribute('role', 'tablist');
    rail.setAttribute('aria-label', 'Cards');

    var dots = deck.cards.map(function (card, i) {
      var dot = el('button', 'depth-dot phase-' + card.phase);
      dot.type = 'button';
      dot.dataset.index = i;
      dot.setAttribute('role', 'tab');
      dot.setAttribute('aria-label', 'Card ' + (i + 1) + ' of ' + total + ': ' + card.title);
      dot.style.setProperty('--depth', card.level / maxLevel);
      rail.appendChild(dot);
      return dot;
    });

    var nextBtn = el('button', 'nav-btn nav-next');
    nextBtn.type = 'button';
    controls.append(prevBtn, rail, nextBtn);

    var railCaption = el('p', 'rail-caption');
    railCaption.appendChild(el('span', null, 'surface'));
    railCaption.appendChild(el('span', 'rail-caption-mid', 'bedrock'));
    railCaption.appendChild(el('span', null, 'rebuilt'));

    var cardsView = el('div', 'view view-cards');
    cardsView.append(status, stack, controls, railCaption);

    /* prose view */
    var proseView = el('div', 'view view-prose hidden');

    /* quiz view */
    var quizView = el('div', 'view view-quiz hidden');

    /* ask bar */
    var askForm = el('form', 'ask-bar');
    var askInput = el('input', 'ask-input');
    askInput.type = 'text';
    askInput.placeholder = 'Ask about this card...';
    askInput.setAttribute('aria-label', 'Ask a question about this card');
    var askBtn = el('button', 'ask-send');
    askBtn.type = 'submit';
    askBtn.setAttribute('aria-label', 'Ask');
    askBtn.appendChild(svg(ICON.send, 15));
    askForm.append(askInput, askBtn);

    askForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var q = askInput.value.trim();
      if (!q) return;
      var card = deck.cards[index];
      close();
      sendMessage(q, { title: card.title, principle: card.principle, chain: card.chain || [] });
    });

    dialog.append(closeBtn, header, tabs, cardsView, proseView, quizView, askForm);
    modal.append(backdrop, dialog);

    var VIEWS = [
      { name: 'cards', tab: cardsTab, panel: cardsView },
      { name: 'prose', tab: proseTab, panel: proseView },
      { name: 'quiz',  tab: quizTab,  panel: quizView },
    ];

    function setView(next) {
      view = next;
      VIEWS.forEach(function (v) {
        var on = v.name === view;
        v.tab.classList.toggle('active', on);
        v.tab.setAttribute('aria-selected', on ? 'true' : 'false');
        v.panel.classList.toggle('hidden', !on);
      });

      if (view === 'prose' && !proseView.childElementCount) {
        proseView.replaceChildren(buildProse(deck, drill));
      }
      if (view === 'quiz') {
        // Rebuilt each time so "Test yourself" always starts fresh.
        quizView.replaceChildren(buildQuiz(deck));
      }

      askForm.classList.toggle('hidden', view === 'quiz');
      askInput.placeholder = view === 'cards'
        ? 'Ask about this card...' : 'Ask about this reasoning...';
      updateScrollHint();
    }

    VIEWS.forEach(function (v) {
      v.tab.addEventListener('click', function () { setView(v.name); });
    });

    function updateScrollHint() {
      var target = view === 'cards' ? face : proseView;
      stack.classList.toggle('has-more',
        view === 'cards' && face.scrollHeight - face.scrollTop - face.clientHeight > 8);
      if (target) { /* prose scrolls in its own container, no hint needed */ }
    }

    face.addEventListener('scroll', updateScrollHint, { passive: true });
    window.addEventListener('resize', updateScrollHint);

    function paint() {
      var card = deck.cards[index];
      face.replaceChildren(buildCardFace(deck, index, drill));
      face.dataset.phase = card.phase;
      face.scrollTop = 0;
      updateScrollHint();

      prevBtn.disabled = index === 0;
      var isLast = index === total - 1;
      nextBtn.replaceChildren(svg(isLast ? ICON.restart : ICON.next, 16, isLast ? 2 : 2.5));
      nextBtn.setAttribute('aria-label', isLast ? 'Back to first card' : 'Next card');

      dots.forEach(function (dot, i) {
        var on = i === index;
        dot.classList.toggle('active', on);
        dot.classList.toggle('seen', i < index);
        dot.setAttribute('aria-selected', on ? 'true' : 'false');
        // Roving tabindex: the rail is one tab stop, not one per card.
        dot.tabIndex = on ? 0 : -1;
      });

      status.textContent = 'Card ' + (index + 1) + ' of ' + total + ', '
        + phaseText(card) + (card.tag ? ', ' + card.tag : '') + ': ' + card.title;

      var remaining = total - index - 1;
      peek1.classList.toggle('hidden', remaining < 1);
      peek2.classList.toggle('hidden', remaining < 2);
    }

    function goTo(next, direction) {
      if (busy || next === index) return;
      var dir = direction || (next > index ? 1 : -1);

      if (reduceMotion.matches) { index = next; paint(); return; }

      busy = true;
      face.classList.remove('enter', 'enter-from-right', 'enter-from-left');
      face.classList.add(dir > 0 ? 'exit-left' : 'exit-right');

      var done = false;
      var timer = null;

      var finish = function (e) {
        if (done || (e && e.target !== face)) return;
        done = true;
        clearTimeout(timer);
        face.removeEventListener('transitionend', finish);
        face.classList.remove('exit-left', 'exit-right');
        index = next;
        paint();

        // Unlock synchronously. Anything that waits on rAF can be throttled
        // (background tab, dropped frame) and would leave the deck stuck
        // ignoring every keypress and swipe from then on.
        busy = false;

        face.classList.add('enter-from-' + (dir > 0 ? 'right' : 'left'));
        requestAnimationFrame(function () {
          face.classList.remove('enter-from-right', 'enter-from-left');
          face.classList.add('enter');
        });
      };

      face.addEventListener('transitionend', finish);
      timer = setTimeout(finish, 260);
    }

    function step(dir) {
      if (view !== 'cards') return;
      if (dir > 0) goTo(index === total - 1 ? 0 : index + 1, 1);
      else if (index > 0) goTo(index - 1, -1);
    }

    function close() {
      modal.remove();
      document.body.classList.remove('modal-open');
      document.removeEventListener('keydown', onKeydown, true);
      window.removeEventListener('resize', updateScrollHint);
      activeModalClose = null;
    }

    function focusables() {
      return Array.prototype.filter.call(
        dialog.querySelectorAll('button:not(:disabled), input'),
        function (node) { return node.offsetParent !== null; }
      );
    }

    function onKeydown(e) {
      // Never hijack arrows/space while the reader is typing a question.
      var typing = document.activeElement === askInput;

      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (!typing) {
        if (e.key === 'ArrowRight') { e.preventDefault(); step(1); return; }
        if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); return; }
        if (e.key === 'Home') { e.preventDefault(); goTo(0, -1); return; }
        if (e.key === 'End') { e.preventDefault(); goTo(total - 1, 1); return; }
      }
      if (e.key !== 'Tab') return;

      var items = focusables();
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      } else if (!dialog.contains(document.activeElement)) {
        e.preventDefault(); first.focus();
      }
    }

    prevBtn.addEventListener('click', function () { step(-1); });
    nextBtn.addEventListener('click', function () { step(1); });

    rail.addEventListener('click', function (e) {
      var dot = e.target.closest('.depth-dot');
      if (dot) goTo(parseInt(dot.dataset.index, 10));
    });

    modal.addEventListener('click', function (e) {
      if (e.target.dataset && e.target.dataset.close === 'true') close();
    });

    face.addEventListener('click', function (e) {
      if (e.target.closest('a, button, input')) return;
      step(1);
    });

    var touchX = 0, touchY = 0;
    dialog.addEventListener('touchstart', function (e) {
      touchX = e.changedTouches[0].screenX;
      touchY = e.changedTouches[0].screenY;
    }, { passive: true });
    dialog.addEventListener('touchend', function (e) {
      var dx = e.changedTouches[0].screenX - touchX;
      var dy = e.changedTouches[0].screenY - touchY;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
    }, { passive: true });

    document.body.classList.add('modal-open');
    document.body.appendChild(modal);
    document.addEventListener('keydown', onKeydown, true);

    paint();
    face.classList.add('enter');
    nextBtn.focus();
    activeModalClose = close;
  }

  /* ── transcript ────────────────────────────────────────────────────── */

  /* The trail is what turns a run of drill-downs into one exploration instead
     of a pile of unrelated decks. */
  function addUserMessage(text, trail) {
    var welcome = document.querySelector('.welcome');
    if (welcome) welcome.remove();

    var block = el('div', 'thread-q');

    if (trail && trail.length) {
      var crumbs = el('nav', 'trail');
      crumbs.setAttribute('aria-label', 'Path drilled through');
      trail.forEach(function (step, i) {
        if (i) crumbs.appendChild(el('span', 'trail-sep', '↓'));
        crumbs.appendChild(el('span', 'trail-step', step));
      });
      block.appendChild(crumbs);
      block.classList.add('is-drill');
    }

    block.appendChild(el('p', 'thread-q-text', text));
    messagesEl.appendChild(block);
    scrollToBottom();
    return block;
  }

  function addPendingDeck() {
    var block = el('div', 'answer is-pending');
    block.appendChild(el('div', 'answer-skeleton'));
    messagesEl.appendChild(block);
    scrollToBottom();
    return block;
  }

  /* How far along a deck is, read off the phase of the last card rather than a
     card count — the total is not known until the deck ends, and a bar that
     guesses a total then has to jump backwards is worse than no bar. Phases
     always run question -> descent -> bedrock -> rebuild -> insight, so the
     phase alone is a truthful position. */
  function streamProgress(cards) {
    var last = cards[cards.length - 1];
    if (!last) return 6;
    switch (last.phase) {
      case 'question': return 12;
      case 'descent':  return Math.min(58, 22 + Number(last.level || 0) * 12);
      case 'bedrock':  return 70;
      case 'rebuild':  return 86;
      case 'insight':  return 96;
      default:         return 40;
    }
  }

  /* The descent, drawn as it is written. Each dot sits at its card's depth, so
     a deck in flight literally shows the chain going down to bedrock and
     climbing back out — which is the thing the app is about. */
  function renderStreamProgress(block, cards) {
    block.classList.remove('is-pending');
    block.classList.add('is-streaming');
    block.replaceChildren();

    block.appendChild(el('span', 'stream-label', 'Decomposing'));

    var bar = el('div', 'stream-bar');
    var fill = el('div', 'stream-bar-fill');
    fill.style.width = streamProgress(cards) + '%';
    bar.appendChild(fill);
    block.appendChild(bar);

    var row = el('div', 'stream-dots');
    cards.forEach(function (card) {
      var dot = el('span', 'stream-dot');
      if (card.phase === 'bedrock') dot.classList.add('is-bedrock');
      dot.style.transform = 'translateY(' + (Number(card.level || 0) * 5) + 'px)';
      row.appendChild(dot);
    });
    block.appendChild(row);

    var latest = cards[cards.length - 1];
    if (latest) {
      block.appendChild(el('p', 'stream-current',
        latest.title || latest.principle || ''));
    }
    scrollToBottom();
  }

  /* NDJSON: one event per line. A chunk can split a line anywhere, so the
     trailing fragment is held back until the rest of it arrives. */
  async function readDeckStream(response, onCard) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var deck = null;

    function handle(line) {
      if (!line.trim()) return;
      var event;
      try { event = JSON.parse(line); } catch (e) { return; }
      if (event.type === 'card' && event.card) onCard(event.card);
      else if (event.type === 'done') deck = event.deck;
    }

    for (;;) {
      var step = await reader.read();
      if (step.done) break;
      buffer += decoder.decode(step.value, { stream: true });
      var lines = buffer.split('\n');
      buffer = lines.pop();
      lines.forEach(handle);
    }
    handle(buffer);

    return deck;
  }

  /* The transcript shows the ANSWER, not a teaser. The chain is visible at a
     glance and the bedrock — the thing the whole deck exists to reach — is
     stated outright. The modal is for reading in depth, not for finding out
     what the deck said. */
  function renderDeck(block, deck) {
    block.classList.remove('is-pending');
    block.replaceChildren();

    var depth = deck.cards.reduce(function (m, c) { return Math.max(m, c.level); }, 0);
    var maxLevel = depth || 1;

    var head = el('div', 'answer-head');
    head.appendChild(el('span', 'answer-topic', deck.topic));

    var meta = el('span', 'answer-meta');
    meta.appendChild(el('span', null, deck.cards.length + ' cards · ' + depth + ' levels deep'));
    if (deck.verified) {
      // "structure", not "chain" or "verified": this checks that the
      // decomposition obeys its own rules, which is not a claim that the
      // reasoning is correct. The badge must not imply the stronger thing.
      var ok = el('span', 'chain-ok');
      ok.appendChild(el('span', 'chain-ok-glyph', '✓'));
      ok.appendChild(el('span', null, 'structure checked'));
      ok.title = 'The decomposition obeys its own rules: one bedrock, a strictly '
               + 'deepening descent, and nothing irreducible above it. This does not '
               + 'check that the claims are true — judge those yourself.';
      meta.appendChild(ok);
    }
    head.appendChild(meta);
    block.appendChild(head);

    /* The thread already shows what was asked. Repeating the deck's wording of
       it is noise unless the model actually reframed the question — which only
       the model can tell us, so it says so in the payload. */
    if (deck.question && deck.reframed) {
      var reframe = el('div', 'answer-reframe');
      reframe.appendChild(el('span', 'answer-reframe-label', 'Reading this as'));
      reframe.appendChild(el('p', 'answer-question', deck.question));
      block.appendChild(reframe);
    }

    /* chain at a glance — each tick is the card's tag at the card's depth */
    var rail = el('div', 'answer-rail');
    deck.cards.forEach(function (card, i) {
      var tick = el('button', 'rail-tick phase-' + card.phase + (card.tag ? ' tag-' + card.tag.toLowerCase() : ''));
      tick.type = 'button';
      tick.style.setProperty('--depth', card.level / maxLevel);
      tick.title = phaseText(card) + (card.tag ? ' · ' + card.tag : '') + ' — ' + card.title;
      tick.setAttribute('aria-label', 'Open card ' + (i + 1) + ': ' + card.title);
      tick.appendChild(el('span', 'rail-glyph', card.tag && TAG_META[card.tag] ? TAG_META[card.tag].glyph : '·'));
      tick.addEventListener('click', function () { openDeck(deck, i); });
      rail.appendChild(tick);
    });
    block.appendChild(rail);

    /* Lay the caption on the same column grid as the ticks so "bedrock" sits
       under the actual bedrock tick instead of floating at the midpoint. */
    var bedIndex = deck.cards.findIndex(function (c) { return c.phase === 'bedrock'; });
    var caption = el('p', 'answer-rail-caption');
    caption.style.gridTemplateColumns = 'repeat(' + deck.cards.length + ', 1fr)';

    var surface = el('span', 'cap-start', 'surface');
    caption.appendChild(surface);

    if (bedIndex > 0 && bedIndex < deck.cards.length - 1) {
      var mid = el('span', 'cap-mid', 'bedrock');
      mid.style.gridColumn = String(bedIndex + 1);
      caption.appendChild(mid);
    }

    var end = el('span', 'cap-end', 'rebuilt');
    end.style.gridColumn = String(deck.cards.length);
    caption.appendChild(end);

    block.appendChild(caption);

    /* An unsound chain must not render as though it passed. Saying so plainly
       is the only honest option for a tool whose claim is rigour. */
    if (!deck.verified) {
      var warn = el('div', 'chain-warning');
      var warnHead = el('div', 'chain-warning-head');
      warnHead.appendChild(el('span', 'chain-warning-glyph', '!'));
      warnHead.appendChild(el('span', null, 'Structure not verified'));
      warn.appendChild(warnHead);
      warn.appendChild(el('p', 'chain-warning-lede',
        'The model broke its own decomposition rules here, and a repair attempt did '
        + 'not fix it. Read this deck with that in mind.'));
      if (deck.issues && deck.issues.length) {
        var list = el('ul', 'chain-warning-list');
        deck.issues.slice(0, 4).forEach(function (issue) {
          list.appendChild(el('li', null, issue));
        });
        warn.appendChild(list);
      }
      block.appendChild(warn);
    }

    /* the payoff: what it actually bottomed out on */
    var bed = deck.cards.filter(function (c) { return c.phase === 'bedrock'; })[0];
    if (bed && bed.principle) {
      var payoff = el('div', 'answer-bedrock');
      var bedHead = el('div', 'answer-bedrock-head');
      bedHead.appendChild(el('span', 'answer-bedrock-label', 'Bottoms out at'));
      var chip = buildTagChip(bed.tag, true);
      if (chip) bedHead.appendChild(chip);
      payoff.appendChild(bedHead);
      payoff.appendChild(el('p', 'answer-bedrock-text', bed.principle));
      block.appendChild(payoff);
    }

    /* Provenance is the honest-labeling rule extended to content: the
       validator only ever checked form, so say out loud whether a human has
       read this deck. */
    var isReviewed = deck.meta && deck.meta.reviewed;
    block.appendChild(el('p',
      'deck-provenance ' + (isReviewed ? 'is-reviewed' : 'is-live'),
      isReviewed ? 'Reviewed deck.'
                 : 'Generated live — structure checked, content unreviewed.'));

    var actions = el('div', 'answer-actions');
    var open = el('button', 'open-deck');
    open.type = 'button';
    open.appendChild(el('span', null, 'Walk the ' + deck.cards.length + ' cards'));
    open.appendChild(el('span', 'open-deck-arrow', '→'));
    open.addEventListener('click', function () { openDeck(deck, 0); });

    var copyBtn = el('button', 'icon-action');
    copyBtn.type = 'button';
    copyBtn.setAttribute('aria-label', 'Copy the full reasoning');
    copyBtn.appendChild(svg(ICON.copy, 14));
    copyBtn.addEventListener('click', function () {
      navigator.clipboard.writeText(deckToText(deck)).then(function () {
        copyBtn.replaceChildren(svg(ICON.check, 14));
        copyBtn.classList.add('copied');
        setTimeout(function () {
          copyBtn.replaceChildren(svg(ICON.copy, 14));
          copyBtn.classList.remove('copied');
        }, 1500);
      });
    });

    actions.append(open, copyBtn);
    block.appendChild(actions);

    if (deck.followups && deck.followups.length) {
      var wrap = el('div', 'followups');
      wrap.appendChild(el('span', 'followups-label', 'Where this leads'));
      var row = el('div', 'followups-row');
      deck.followups.forEach(function (q) {
        var chip2 = el('button', 'followup-chip');
        chip2.type = 'button';
        chip2.appendChild(el('span', 'followup-arrow', '→'));
        chip2.appendChild(el('span', null, q));
        chip2.addEventListener('click', function () { sendMessage(q); });
        row.appendChild(chip2);
      });
      wrap.appendChild(row);
      block.after(wrap);
      state.followupsEl = wrap;
    }

    scrollToBottom();
  }

  function addRegenerateButton(deckEl) {
    if (state.regenBar) state.regenBar.remove();

    var bar = el('div', 'regenerate-bar');
    var btn = el('button', 'regenerate-btn');
    btn.type = 'button';
    btn.appendChild(svg(ICON.restart, 14));
    btn.appendChild(el('span', null, 'Regenerate'));

    btn.addEventListener('click', function () {
      if (state.isWaiting || !state.lastQuestion) return;
      // The question heading has to go too — sendMessage re-adds it, and
      // leaving it behind printed the question twice per regenerate.
      if (state.lastQuestionEl) state.lastQuestionEl.remove();
      if (state.lastDeckEl) state.lastDeckEl.remove();
      if (state.followupsEl) { state.followupsEl.remove(); state.followupsEl = null; }
      bar.remove();
      state.lastQuestionEl = null;
      state.lastDeckEl = null;
      state.regenBar = null;

      // Regenerating replaces a turn, so every record of it has to roll back
      // too. Without this the stored thread keeps the discarded turn and a
      // reload resurrects the answer that was just thrown away.
      var focus = state.lastFocus;
      state.turns.pop();
      if (focus) state.trail = state.trail.slice(0, -1);
      saveThread();

      state.history.pop();   // drop the assistant turn we just removed
      state.history.pop();   // drop the user turn; sendMessage re-adds it
      sendMessage(state.lastQuestion, focus);
    });

    bar.appendChild(btn);
    (state.followupsEl || deckEl).after(bar);
    state.regenBar = bar;
    state.lastDeckEl = deckEl;
  }

  function deckSummary(deck) {
    var lines = ['Deck: ' + deck.topic, deck.question];
    deck.cards.forEach(function (c, i) {
      lines.push((i + 1) + '. [' + (c.tag || c.phase) + '] ' + c.title + ' — ' + c.principle);
    });
    return lines.join('\n');
  }

  /* ── send ──────────────────────────────────────────────────────────── */

  var slowTimer = null;

  function showTyping() {
    var label = typingEl.querySelector('.typing-label');
    if (label) label.textContent = 'Decomposing to first principles...';
    typingEl.classList.remove('hidden');
    // A 30s wait with a static label is indistinguishable from a hang.
    clearTimeout(slowTimer);
    slowTimer = setTimeout(function () {
      if (label) label.textContent = 'Still working — a deep chain can take a minute';
    }, SLOW_NOTICE_MS);
    scrollToBottom();
  }

  function hideTyping() {
    clearTimeout(slowTimer);
    typingEl.classList.add('hidden');
  }

  function setSendMode(mode) {
    var stopping = mode === 'stop';
    sendBtn.replaceChildren(svg(stopping ? ICON.stop : ICON.send, 20, stopping ? 0 : 2));
    if (stopping) sendBtn.querySelector('svg').setAttribute('fill', 'currentColor');
    sendBtn.classList.toggle('is-stop', stopping);
    sendBtn.setAttribute('aria-label', stopping ? 'Stop generating' : 'Send question');
    sendBtn.disabled = stopping ? false : !inputEl.value.trim();
  }

  async function sendMessage(text, focus) {
    if (state.isWaiting || !text.trim()) return;

    if (state.followupsEl) { state.followupsEl.remove(); state.followupsEl = null; }
    if (state.regenBar) { state.regenBar.remove(); state.regenBar = null; }

    // A drill extends the path; a fresh question roots a new one. Seeding the
    // root with the question itself is what lets the breadcrumb show the whole
    // descent — "why is the sky blue -> air redirects light -> ..." — rather
    // than only the hops after the first.
    var trail = focus
      ? state.trail.concat([focus.title || focus.principle || 'this claim'])
      : [text];
    state.trail = trail;

    state.lastQuestion = text;
    state.lastFocus = focus || null;
    state.isWaiting = true;
    state.abortReason = null;
    inputEl.disabled = true;
    setSendMode('stop');
    hideToast();

    state.lastQuestionEl = addUserMessage(text, trail.slice(0, -1));
    state.history.push({ role: 'user', content: text });

    var deckEl = addPendingDeck();
    showTyping();

    var deadline = null;

    try {
      state.abortController = new AbortController();
      deadline = setTimeout(function () {
        state.abortReason = 'timeout';
        if (state.abortController) state.abortController.abort();
      }, CLIENT_DEADLINE_MS);

      // Only the last 20 are used server-side, and the request body is capped.
      // Sending the whole session would grow without bound.
      var body = { message: text, history: state.history.slice(0, -1).slice(-20) };
      if (focus) body.focus = focus;

      var response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: state.abortController.signal,
      });

      if (!response.ok) {
        var detail = 'Request failed with status ' + response.status;
        try {
          var errData = await response.json();
          if (errData && errData.detail) {
            // FastAPI sends a plain string for our own errors and an object for
            // validation failures; only the latter is worth stringifying.
            detail = typeof errData.detail === 'string'
              ? errData.detail : JSON.stringify(errData.detail);
          }
        } catch (e) { /* non-JSON error body */ }

        // A gated instance. Ask for the token here rather than in a toast, so
        // the reader does not lose the question they just typed. Unwinding
        // through the catch keeps the `finally` cleanup in one place.
        if (response.status === 401) {
          var gateErr = new Error(detail);
          gateErr.name = 'AccessError';
          gateErr.granted = await requestAccess();
          throw gateErr;
        }

        // Being rate limited is not a failure to retry into — the retry would
        // be refused too, and the message already says when to come back.
        if (response.status === 429) {
          showToast(detail);
        } else {
          showToast(detail, { label: 'Retry', cb: function () { sendMessage(text, focus); } });
        }
        throw new Error(detail);
      }

      // Cards render as they close; the deck that arrives last is the one that
      // has been validated, and it is what finally gets rendered.
      var streamed = [];
      var deck = await readDeckStream(response, function (card) {
        if (!streamed.length) hideTyping();
        streamed.push(card);
        renderStreamProgress(deckEl, streamed);
      });

      if (!deck) throw new Error('The connection closed before the deck finished.');

      deckEl.classList.remove('is-streaming');
      renderDeck(deckEl, deck);
      state.history.push({ role: 'assistant', content: deckSummary(deck) });
      addRegenerateButton(deckEl);
      state.turns.push({
        q: text, deck: deck, trail: trail.slice(0, -1), focus: focus || null,
      });
      saveThread();
      if (newThreadBtn) newThreadBtn.hidden = false;
    } catch (err) {
      state.history.pop();   // no assistant turn was recorded

      if (err.name === 'AccessError') {
        // Leave no wreckage either way: the question is re-added by the retry,
        // and a reader who declined should not be staring at a dead deck.
        if (state.lastQuestionEl) state.lastQuestionEl.remove();
        state.lastQuestionEl = null;
        deckEl.remove();

        if (err.granted) {
          // The `finally` below is what clears isWaiting, and sendMessage
          // returns early while it is set — so the retry has to follow it.
          setTimeout(function () { sendMessage(text, focus); }, 0);
        } else {
          showToast('This instance requires an access token.', {
            label: 'Enter token',
            cb: function () {
              requestAccess().then(function (ok) { if (ok) sendMessage(text, focus); });
            },
          });
        }
        return;
      }

      if (err.name === 'AbortError') {
        if (state.abortReason === 'timeout') {
          // Distinguish "gave up" from "you stopped it" — they need different
          // responses from the reader.
          deckEl.classList.remove('is-pending');
          deckEl.replaceChildren(el('p', 'deck-error',
            'This took longer than ' + Math.round(CLIENT_DEADLINE_MS / 1000)
            + ' seconds and was given up on. The model may be overloaded.'));
          showToast('The request timed out', {
            label: 'Try again',
            cb: function () { deckEl.remove(); sendMessage(text, focus); },
          });
        } else {
          // Stopped deliberately: leave no wreckage behind.
          if (state.lastQuestionEl) state.lastQuestionEl.remove();
          state.lastQuestionEl = null;
          deckEl.remove();
        }
        return;
      }

      deckEl.classList.remove('is-pending');
      deckEl.replaceChildren(el('p', 'deck-error', err.message || 'Something went wrong.'));
    } finally {
      clearTimeout(deadline);
      state.isWaiting = false;
      state.abortReason = null;
      inputEl.disabled = false;
      setSendMode('send');
      inputEl.focus();
      hideTyping();
      state.abortController = null;
    }
  }

  /* ── input ─────────────────────────────────────────────────────────── */

  function handleInput() {
    if (!state.isWaiting) sendBtn.disabled = !inputEl.value.trim();
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  }

  function submit() {
    // While a deck is in flight the same button is the Stop control.
    if (state.isWaiting) {
      state.abortReason = 'user';
      if (state.abortController) state.abortController.abort();
      return;
    }
    var text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    inputEl.style.height = 'auto';
    sendBtn.disabled = true;
    sendMessage(text);
  }

  inputEl.addEventListener('input', handleInput);
  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  form.addEventListener('submit', function (e) { e.preventDefault(); submit(); });

  messagesEl.addEventListener('click', function (e) {
    var chip = e.target.closest('.suggestion-chip');
    if (chip && chip.dataset.prompt) sendMessage(chip.dataset.prompt);
  });

  /* ── persistence ───────────────────────────────────────────────────────
     A reload used to lose the whole thread, which is punishing when each
     answer costs a model call. Decks are plain JSON, so the thread stores
     and replays exactly. */

  function saveThread() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        turns: state.turns.slice(-MAX_STORED_TURNS),
        history: state.history.slice(-40),
        trail: state.trail,
      }));
    } catch (e) {
      // Quota exceeded or storage disabled. Losing history is not worth
      // interrupting the reader over.
    }
  }

  function clearThread() {
    try { localStorage.removeItem(STORE_KEY); } catch (e) {}
    state.turns = [];
    state.history = [];
    state.trail = [];
    state.lastDeckEl = null;
    state.regenBar = null;
    state.followupsEl = null;
    location.reload();
  }

  function restoreThread() {
    var raw;
    try { raw = localStorage.getItem(STORE_KEY); } catch (e) { return false; }
    if (!raw) return false;

    var saved;
    try { saved = JSON.parse(raw); } catch (e) { return false; }
    if (!saved || !Array.isArray(saved.turns) || !saved.turns.length) return false;

    state.history = Array.isArray(saved.history) ? saved.history : [];
    state.trail = Array.isArray(saved.trail) ? saved.trail : [];

    saved.turns.forEach(function (turn) {
      if (!turn || !turn.deck || !turn.deck.cards) return;
      state.turns.push(turn);
      state.lastQuestionEl = addUserMessage(turn.q, turn.trail);
      var block = el('div', 'answer');
      messagesEl.appendChild(block);
      renderDeck(block, turn.deck);
      // Carried so a restored drill-down regenerates as a drill, not as a
      // fresh question that has lost its focus.
      state.lastQuestion = turn.q;
      state.lastFocus = turn.focus || null;
      addRegenerateButton(block);
    });

    if (newThreadBtn) newThreadBtn.hidden = false;
    scrollToBottom();
    return true;
  }

  var newThreadBtn = document.getElementById('new-thread');
  if (newThreadBtn) {
    newThreadBtn.addEventListener('click', function () {
      if (state.isWaiting) return;
      clearThread();
    });
  }

  initExplore();
  renderReviewBanner();
  handleInput();

  if (restoreThread()) {
    var welcome = document.querySelector('.welcome');
    if (welcome) welcome.remove();
  }
})();
