"""Run the real deck workload against every configured provider.

    python tools/check_providers.py

A provider's docs will tell you it supports JSON mode. They will not tell you
whether it can hold this app's contract -- exactly one bedrock, strictly
increasing descent, no ATOMIC above the floor. Small models return valid JSON
that fails validate_chain, which looks like a bug and is not one. The only
honest test is the workload itself.

Costs one deck per provider. Nothing here is imported by the app; it composes
functions that are already covered by tests/.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import main

PROBE = "Why do planes actually stay up?"


def verdict(deck) -> str:
    depth = max((c["level"] for c in deck["cards"]), default=0)
    has_bedrock = any(c["phase"] == "bedrock" for c in deck["cards"])

    if not deck["verified"]:
        return "USABLE-ISH: valid JSON, but the chain broke its own rules"
    if depth < 3 or not has_bedrock:
        return "WEAK: valid chain but shallow — the method barely pays off here"
    return "GOOD: deep chain, proper bedrock, passes validation"


async def probe(provider: dict) -> None:
    label = f"{provider['name']}/{provider['model']}"
    client = main.get_client(provider["api_key"], provider["endpoint"])
    messages = main.build_messages(main.ChatRequest(message=PROBE))

    started = time.time()
    try:
        deck, _ = await main.request_deck(client, provider["model"], messages)
    except Exception as exc:
        detail = str(exc)
        # The two failures worth naming, because they mean different actions.
        if "response_format" in detail or "json_object" in detail:
            note = "NO STRUCTURED OUTPUT — unusable, pick another model"
        elif "429" in detail:
            note = "RATE LIMITED — fine as a fallback, not as the first hop"
        elif "402" in detail or "credit" in detail.lower():
            note = "OUT OF CREDIT"
        else:
            note = detail[:110]
        print(f"  {label:<52} {time.time()-started:5.1f}s  FAILED  {note}")
        return

    depth = max((c["level"] for c in deck["cards"]), default=0)
    print(f"  {label:<52} {time.time()-started:5.1f}s  "
          f"cards={len(deck['cards'])} depth={depth} "
          f"verified={str(deck['verified']):<5} {verdict(deck)}")
    for issue in deck["issues"][:2]:
        print(f"       ! {issue[:100]}")


async def main_async() -> int:
    providers = main.get_providers()
    if not providers:
        print("No providers configured. Set PROVIDERS in .env — see .env.example.")
        return 1

    print(f"Probing {len(providers)} provider(s) with: {PROBE!r}\n")
    for provider in providers:
        await probe(provider)

    print("\nPut the GOOD ones first in PROVIDERS. Anything reporting NO "
          "STRUCTURED OUTPUT cannot be used at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
