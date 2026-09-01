"""LLM call to generate a Plan, then expand to a full Deck.

New fast path: one small call (~200-300 tokens out), guaranteed valid by construction.
Old path (request_deck + attempt_repair + fallback) remains untouched.
"""

import asyncio
import json
import os
from typing import List, Optional

from fpb.config import DECK_MAX_TOKENS, DECK_TIMEOUT_SECONDS, get_config
from fpb.telemetry import COUNTERS, LOG
from fpb.plan import Plan, PlanCard, expand_plan_to_deck, build_plan_prompt


async def generate_plan(client, model: str, messages: List[dict], max_tokens: int) -> dict:
    """Call the LLM with the compact Plan prompt and return the parsed Plan dict."""
    cfg = get_config()
    system = (
        "You output ONLY the JSON object described in the user prompt. "
        "No markdown, no explanation, no extra fields. "
        "The JSON must match the schema exactly."
    )

    # Build compact plan prompt (much shorter than the full deck prompt)
    user = build_plan_prompt(messages[-1]["content"])

    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=min(max_tokens, 1500),  # Plan is small
            temperature=0.3,
            timeout=DECK_TIMEOUT_SECONDS,
        ),
        timeout=DECK_TIMEOUT_SECONDS,
    )

    content = resp.choices[0].message.content
    if not content:
        raise ValueError("Empty completion")

    data = json.loads(content)
    return data


async def attempt_plan_expand(
    client,
    model: str,
    messages: List[dict],
    max_tokens: int,
) -> dict:
    """Fast path: one LLM call → Plan → deterministic Deck expansion.

    Returns a normalized, verified deck dict ready to serve.
    On any error, raises so caller can fall back to old path.
    """
    plan_data = await generate_plan(client, model, messages, max_tokens)

    # Build Plan object (validate structure)
    plan = Plan(
        topic=plan_data.get("topic", "Untitled"),
        question=plan_data.get("question", messages[-1]["content"]),
        descent_depth=int(plan_data.get("descent_depth", 3)),
        cards=[
            PlanCard(
                phase=c.get("phase", "descent"),
                level=c.get("level", 0),
                title=c.get("title", ""),
                principle=c.get("principle", ""),
                question=c.get("question", ""),
                tag=c.get("tag", ""),
                chain=c.get("chain", []),
                discarded=c.get("discarded", []),
                explanation=c.get("explanation", ""),
                takeaway=c.get("takeaway", ""),
            )
            for c in plan_data.get("cards", [])
        ],
        reframed=plan_data.get("reframed", False),
        followups=plan_data.get("followups", []),
    )

    # Deterministic expansion (cannot fail validation)
    deck = expand_plan_to_deck(plan)
    COUNTERS.bump("deck.plan_expand_ok")
    LOG.info("plan-expand: deck generated for %s/%s", "provider", model)
    return deck