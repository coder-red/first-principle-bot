"""Generate library decks from library/topics.txt.

    python tools/build_library.py [--dry-run]

Put the strongest model you have FIRST in PROVIDERS when running this — the
deployed app never needs that key, only the generated files. Each topic
costs one deck call (plus one repair call if the chain needs fixing).

Only decks that pass validate_chain are written, and an existing slug is
never overwritten — re-running tops up whatever topics.txt has grown.
`reviewed: true` is written here and nowhere else; the human step is reading
the deck before `git add`.
"""

import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import main
from fpb.library import LIBRARY_ROOT, save_deck, slug_for
from fpb.providers import get_client
from fpb.schemas import ChatRequest, build_messages

TOPICS = os.path.join("library", "topics.txt")


def read_topics():
    pairs = []
    with open(TOPICS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            sector, question = line.split(":", 1)
            pairs.append((sector.strip(), question.strip()))
    return pairs


async def build_one(providers, question):
    req = ChatRequest(message=question)
    messages = build_messages(req)
    for provider in providers:
        client = get_client(provider["api_key"], provider["endpoint"])
        try:
            deck, raw = await main.request_deck(
                client, provider["model"], messages, provider["max_tokens"])
            if not deck["verified"]:
                deck = await main.attempt_repair(
                    client, provider["model"], messages, raw, deck,
                    provider["max_tokens"])
            return deck, provider["model"]
        except Exception as exc:
            print(f"    {provider['name']}/{provider['model']}: {exc}")
    return None, None


async def run(dry_run: bool):
    providers = main.get_providers()
    if not providers:
        sys.exit("No providers configured. Set PROVIDERS in .env first.")
    written, skipped, failed = 0, 0, 0
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    for sector, question in read_topics():
        slug = slug_for(question)
        if os.path.exists(os.path.join(LIBRARY_ROOT, slug + ".json")):
            skipped += 1
            continue
        print(f"[{sector}] {question}")
        if dry_run:
            print(f"    would write {slug}.json")
            continue
        deck, model = await build_one(providers, question)
        if deck is None or save_deck(LIBRARY_ROOT, question, sector, deck,
                                     model, stamp) is None:
            failed += 1
            print("    FAILED - not written (unverified or all providers errored)")
        else:
            written += 1
            print(f"    wrote {slug}.json  ({model})")
    print(f"\nwritten {written}, skipped {skipped} existing, failed {failed}")
    print("Now READ each new deck before committing it. Review is you.")


if __name__ == "__main__":
    asyncio.run(run("--dry-run" in sys.argv))
