"""EXPLORE — a question worth decomposing, for people who cannot think of one.

The blank box is the real bottleneck in this app: the method is sharp, but
composing a question whose conventional answer is itself a convention is a
skill. So the model writes them, per sector, and no question is served twice.

The generator lives in main.py, because it walks the provider chain and so has
to see the patchable module-level pool. Everything here is pure.
"""

from typing import Dict, List, Optional

from fpb.deck import _text

SECTORS = [
    {"slug": "career",     "label": "Career",     "blurb": "work, money, status"},
    {"slug": "health",     "label": "Health",     "blurb": "the body, sleep, food"},
    {"slug": "football",   "label": "Football",   "blurb": "the game, the physics"},
    {"slug": "history",    "label": "History",    "blurb": "how we got here"},
    {"slug": "money",      "label": "Money",      "blurb": "value, price, debt"},
    {"slug": "mind",       "label": "Mind",       "blurb": "memory, belief, self"},
    {"slug": "physics",    "label": "Physics",    "blurb": "matter, light, time"},
    {"slug": "technology", "label": "Technology", "blurb": "machines that think"},
    {"slug": "everyday",   "label": "Everyday",   "blurb": "the ordinary, examined"},
]

# How many to show at once, and the mark below which the pool is topped up.
EXPLORE_PAGE = 6
EXPLORE_LOW_WATER = 6
EXPLORE_BATCH = 12


def sector_by_slug(slug: str) -> Optional[dict]:
    return next((s for s in SECTORS if s["slug"] == slug), None)


def _looks_like_a_question(text: str) -> bool:
    """A topic heading is not a question, and the deck prompt needs a question."""
    return text.endswith("?") and len(text) >= 12


def normalize_questions(raw) -> List[dict]:
    """Keep the entries that clear the bar; drop the rest silently.

    The generator is a cheap model and will return headings, fragments and the
    occasional bare string. None of that is worth showing.
    """
    if not isinstance(raw, list):
        return []

    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = _text(item.get("question"))
        if not _looks_like_a_question(question):
            continue
        out.append({"question": question, "hook": _text(item.get("hook"))})
    return out


def _key(question: str) -> str:
    return " ".join(question.lower().split())


class ExplorePool:
    """Questions waiting to be shown for one sector, and every one already
    shown. The generator is asked to avoid repeats and will repeat anyway, so
    the pool is the backstop rather than the prompt."""

    def __init__(self, slug: str = "", store=None):
        self._waiting: List[dict] = []
        self._served: List[str] = []
        self._seen: set = set()
        # Optional fpb.store.StateStore. With one, the served set outlives the
        # process, so a restart does not start handing out the same questions
        # again — which is the failure the pool exists to prevent.
        self._slug = slug
        self._store = store

    def add(self, questions: List[dict]) -> None:
        if self._store is not None:
            self._store.add_questions(self._slug, questions)
            return
        for item in questions:
            key = _key(item["question"])
            if key in self._seen:
                continue
            self._seen.add(key)
            self._waiting.append(item)

    def take(self, count: int) -> List[dict]:
        if self._store is not None:
            return self._store.take_questions(self._slug, count)
        taken, self._waiting = self._waiting[:count], self._waiting[count:]
        self._served.extend(item["question"] for item in taken)
        return taken

    def needs_refill(self, low_water: int) -> bool:
        if self._store is not None:
            return self._store.waiting_count(self._slug) < low_water
        return len(self._waiting) < low_water

    def avoid_list(self, limit: int = 40) -> List[str]:
        """The most recently served questions, to steer the next batch away."""
        if self._store is not None:
            return self._store.served_questions(self._slug, limit)
        return self._served[-limit:]


EXPLORE_POOLS: Dict[str, ExplorePool] = {}
