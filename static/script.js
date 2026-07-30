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
  };

  /* ── theme ─────────────────────────────────────────────────────────── */

  function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('fp_theme', t);
  }

  setTheme(localStorage.getItem('fp_theme') || 'dark');
  themeBtn.addEventListener('click', function () {
    setTheme(getTheme() === 'dark' ? 'light' : 'dark');
  });

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
     decomposing them actually pays off, which is the point of the app.
     No emoji: newer codepoints render as tofu on Windows, and a precise tool
     reads better without them. */
  var QUESTION_POOL = [
    'Why do planes actually stay up?',
    'Why does a mirror flip left to right but not up and down?',
    'What is money, really?',
    'What is fire?',
    'Why can nothing travel faster than light?',
    'Why do we have to sleep?',
    'Why does ice float when almost every other solid sinks?',
    'Why does music sound good?',
    'Why can you not tickle yourself?',
    'What is actually moving when electricity flows?',
    'How do we know the Earth is round without leaving it?',
    'Why is glass transparent?',
    'How does a magnet pull on something it never touches?',
    'Why is the sky blue?',
    'Why can we not remember being a baby?',
    'What is time?',
    'How does anaesthesia switch consciousness off?',
    'Why is the sea salty but rivers are not?',
  ];

  var poolCursor = Math.floor(Math.random() * QUESTION_POOL.length);

  function renderSuggestions() {
    if (!suggestionsEl) return;
    var picks = [];
    for (var i = 0; i < 4; i++) {
      picks.push(QUESTION_POOL[(poolCursor + i) % QUESTION_POOL.length]);
    }
    poolCursor = (poolCursor + 4) % QUESTION_POOL.length;

    suggestionsEl.replaceChildren();
    picks.forEach(function (question) {
      var chip = el('button', 'suggestion-chip');
      chip.type = 'button';
      chip.dataset.prompt = question;
      chip.appendChild(el('span', 'chip-text', question));
      chip.appendChild(el('span', 'chip-arrow', '→'));
      suggestionsEl.appendChild(chip);
    });
  }

  if (shuffleBtn) {
    shuffleBtn.addEventListener('click', function () {
      renderSuggestions();
      suggestionsEl.classList.remove('reshuffled');
      void suggestionsEl.offsetWidth;
      suggestionsEl.classList.add('reshuffled');
    });
  }

  /* ── epistemic tags ────────────────────────────────────────────────── */

  var TAG_META = {
    ATOMIC:     { glyph: '◆', label: 'Atomic',     hint: 'Irreducible — cannot be broken down further' },
    VERIFIED:   { glyph: '✓', label: 'Verified',   hint: 'Empirically confirmed, but could be otherwise' },
    CONVENTION: { glyph: '≈', label: 'Convention', hint: 'Widely accepted, not proven' },
    ASSUMPTION: { glyph: '○', label: 'Assumption', hint: 'Taken for granted' },
    UNKNOWN:    { glyph: '?', label: 'Unknown',    hint: 'Not known — reasoning stops here' },
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

  function buildQuiz(deck) {
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

    var questions = quizQuestions(deck);
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
      body.appendChild(el('p', 'quiz-ask', 'How well is this known?'));

      var opts = el('div', 'quiz-options');
      var answered = false;

      q.options.forEach(function (tag) {
        var meta = TAG_META[tag];
        var btn = el('button', 'quiz-option');
        btn.type = 'button';
        btn.appendChild(el('span', 'quiz-option-glyph tag-text-' + tag.toLowerCase(), meta.glyph));
        btn.appendChild(el('span', 'quiz-option-label', meta.label));
        btn.addEventListener('click', function () {
          if (answered) return;
          answered = true;
          var right = tag === q.card.tag;
          if (right) matched++;

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
    face.setAttribute('aria-live', 'polite');
    stack.append(peek2, peek1, face);

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
    cardsView.append(stack, controls, railCaption);

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
        dot.classList.toggle('active', i === index);
        dot.classList.toggle('seen', i < index);
        dot.setAttribute('aria-selected', i === index ? 'true' : 'false');
      });

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
      var ok = el('span', 'chain-ok');
      ok.appendChild(el('span', 'chain-ok-glyph', '✓'));
      ok.appendChild(el('span', null, 'chain checked'));
      ok.title = 'The decomposition follows its own rules: one bedrock, strictly '
               + 'deepening descent, and nothing irreducible above it.';
      meta.appendChild(ok);
    }
    head.appendChild(meta);
    block.appendChild(head);

    if (deck.question) block.appendChild(el('p', 'answer-question', deck.question));

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
      warnHead.appendChild(el('span', null, 'Chain not verified'));
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

  function showTyping() {
    typingEl.classList.remove('hidden');
    scrollToBottom();
  }

  function hideTyping() {
    typingEl.classList.add('hidden');
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
    sendBtn.disabled = true;
    inputEl.disabled = true;
    hideToast();

    state.lastQuestionEl = addUserMessage(text, trail.slice(0, -1));
    state.history.push({ role: 'user', content: text });

    var deckEl = addPendingDeck();
    showTyping();

    try {
      state.abortController = new AbortController();

      var body = { message: text, history: state.history.slice(0, -1) };
      if (focus) body.focus = focus;

      var response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: state.abortController.signal,
      });

      if (!response.ok) {
        var detail = 'Request failed with status ' + response.status;
        try {
          var errData = await response.json();
          if (errData && errData.detail) detail = JSON.stringify(errData.detail);
        } catch (e) { /* non-JSON error body */ }
        showToast(detail, { label: 'Retry', cb: function () { sendMessage(text, focus); } });
        throw new Error(detail);
      }

      var deck = await response.json();
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
      if (err.name === 'AbortError') {
        deckEl.remove();
        return;
      }
      deckEl.classList.remove('is-pending');
      deckEl.replaceChildren(el('p', 'deck-error', err.message || 'Something went wrong.'));
    } finally {
      state.isWaiting = false;
      sendBtn.disabled = !inputEl.value.trim();
      inputEl.disabled = false;
      inputEl.focus();
      hideTyping();
      state.abortController = null;
    }
  }

  /* ── input ─────────────────────────────────────────────────────────── */

  function handleInput() {
    sendBtn.disabled = !inputEl.value.trim() || state.isWaiting;
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  }

  function submit() {
    var text = inputEl.value.trim();
    if (!text || state.isWaiting) return;
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

  renderSuggestions();
  handleInput();

  if (restoreThread()) {
    var welcome = document.querySelector('.welcome');
    if (welcome) welcome.remove();
  }
})();
