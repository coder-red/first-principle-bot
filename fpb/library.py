"""The deck library: decks generated once, validated, reviewed, committed.

The repo is the CMS. A file in library/decks/ IS a published deck — there is
no admin surface and no database. Loading happens once at startup; the app
never reads the directory again, so a deploy is the only way to publish,
which is exactly the review gate the spec wants.
"""

import json
import os
import re
from typing import List, Optional

from fpb.deck import normalize_deck
from fpb.telemetry import LOG

LIBRARY_ROOT = os.environ.get("LIBRARY_ROOT", os.path.join("library", "decks"))


def slug_for(question: str) -> str:
    """A filesystem- and URL-safe name derived from the question."""
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    return slug[:60].rstrip("-")


class Library:
    def __init__(self, decks: dict):
        self._decks = decks  # slug -> normalized deck with meta attached

    @classmethod
    def load(cls, root: str) -> "Library":
        """Read every deck in root. A malformed file is a logged warning, not
        a crash: one bad commit must not take the whole library down."""
        decks = {}
        if not os.path.isdir(root):
            return cls(decks)
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                meta = data.get("meta")
                if not isinstance(meta, dict) or not meta.get("slug"):
                    raise ValueError("missing meta.slug")
                deck = normalize_deck(data)
                deck["meta"] = {
                    "slug": str(meta["slug"]),
                    "sector": str(meta.get("sector", "")),
                    "model": str(meta.get("model", "")),
                    "generated_at": str(meta.get("generated_at", "")),
                    "reviewed": meta.get("reviewed") is True,
                }
                decks[deck["meta"]["slug"]] = deck
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                LOG.warning("library: skipping %s: %s", path, exc)
        return cls(decks)

    def count(self) -> int:
        return len(self._decks)

    def index(self) -> List[dict]:
        entries = [
            {"slug": d["meta"]["slug"], "sector": d["meta"]["sector"],
             "topic": d["topic"], "question": d["question"]}
            for d in self._decks.values()
        ]
        return sorted(entries, key=lambda e: (e["sector"], e["topic"]))

    def deck(self, slug: str) -> Optional[dict]:
        return self._decks.get(slug)

    def decks(self) -> List[dict]:
        """Every loaded deck, in slug order (used by the instant matcher)."""
        return list(self._decks.values())


def save_deck(root: str, question: str, sector: str, deck: dict,
              model: str, generated_at: str) -> Optional[str]:
    """Write a deck into the library, or refuse.

    The two refusals are the whole point: an unverified deck must never gain
    `reviewed: true`, and a re-run of the build tool must never silently
    replace a deck a human already read.
    """
    if not deck.get("verified"):
        return None
    slug = slug_for(question)
    path = os.path.join(root, slug + ".json")
    if os.path.exists(path):
        return None
    payload = dict(deck)
    payload["meta"] = {"slug": slug, "sector": sector, "model": model,
                      "generated_at": generated_at, "reviewed": True}
    os.makedirs(root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return slug
