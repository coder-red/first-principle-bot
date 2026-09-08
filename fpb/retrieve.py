"""Instant lookups: a question that matches a reviewed deck is served from the
library instead of spending a model call.

The library is small and curated, so matching is lexical and deterministic:
exact-phrase first, then content-word containment with a shared-word floor.
There is no embedding call — that would cost latency and credits, which is the
whole point of this path. Conservative by design: a near miss falls through to
normal generation rather than risk serving the wrong deck.
"""

import re
from typing import Optional

from fpb.deck import QUESTION_STOPWORDS
from fpb.library import Library, slug_for

_TOKEN_RE = re.compile(r"[a-z0-9']+")

EXTRA_STOPWORDS = frozenset(
    "tell me know want we us our yours explain describe remember thanks hello "
    "hi please ask".split()
)


def _stem(word: str) -> str:
    """Strip a trailing plural 's'. Enough to bridge 'empires'/'empire' and
    'fall/collapse' style rephrasing without a full stemmer dependency."""
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _content_tokens(text: str) -> set:
    return {
        _stem(word) for word in _TOKEN_RE.findall(text.lower())
        if word not in QUESTION_STOPWORDS and word not in EXTRA_STOPWORDS
    }


def _deck_vocab(deck: dict) -> set:
    """What the deck is about: its question, topic, and the slug — the slug is
    the short-form phrasing the deck was built from, so it carries synonyms a
    rephrased question is likely to use (fall vs collapse)."""
    meta = deck.get("meta", {})
    return _content_tokens(
        deck["question"] + " " + deck.get("topic", "") + " " + str(meta.get("slug", ""))
    )


def match_score(question: str, deck: dict) -> float:
    """How much of the reader's question the deck's vocabulary covers.

    1.0 = every content word in the question appears in the deck's question,
    topic or slug. Lower means parts of the ask are outside the deck.
    """
    q = _content_tokens(question)
    if not q:
        return 0.0
    d = _deck_vocab(deck)
    if not d:
        return 0.0
    return len(q & d) / len(q)


def find_library_match(question: str, library: Library,
                       min_score: float = 0.75) -> Optional[dict]:
    """The best reviewed deck for `question`, or None to generate normally.

    Exact-phrase (slug) matches win outright; otherwise the deck whose question,
    topic and slug vocabulary covers the most of the ask, provided the reader's
    content words are almost all inside that deck. A question that adds words
    outside the deck (a qualifier, a different subject) drops below the bar and
    falls through to normal generation rather than risk the wrong deck. The
    bar is high on purpose: sharing one keyword is not enough to claim the
    deck answers the question asked.
    """
    exact = library.deck(slug_for(question))
    if exact is not None and exact.get("meta", {}).get("reviewed"):
        return exact

    best, best_score = None, 0.0
    for deck in library.decks():
        if not deck.get("meta", {}).get("reviewed"):
            continue
        score = match_score(question, deck)
        if score > best_score:
            best, best_score = deck, score

    if best is not None and best_score >= min_score:
        return best
    return None