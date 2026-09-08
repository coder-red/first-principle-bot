"""LLM call to generate a Plan, then expand to a full Deck.

New fast path: one small call (~200-300 tokens out), guaranteed valid by construction.
Old path (request_deck + attempt_repair + fallback) remains untouched.
"""

import asyncio
import json
from typing import Optional

from fpb.config import DECK_TIMEOUT_SECONDS
from fpb.telemetry import COUNTERS, LOG
from fpb.plan import Plan, PlanCard, expand_plan_to_deck, build_plan_prompt


async def generate_plan(client, model: str, question: str, max_tokens: int) -> dict:
    """Call the LLM with the compact Plan prompt and return the parsed Plan dict.

    `question` is the reader's actual question, not a prompt-instruction string.
    """
    system = (
        "You output ONLY the JSON object described in the user prompt. "
        "No markdown, no explanation, no extra fields. "
        "The JSON must match the schema exactly."
    )

    # Build compact plan prompt (much shorter than the full deck prompt)
    user = build_plan_prompt(question)

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
    question: str,
    max_tokens: int,
) -> dict:
    """Fast path: one LLM call → Plan → deterministic Deck expansion.

    Returns a normalized, verified deck dict ready to serve.
    On any error, raises so caller can fall back to old path.

    `question` is the reader's actual question (from ChatRequest.message). The
    full-message list from `build_messages` ends with a "Build the deck now..."
    instruction, so it must not be used as the question — that would decompose
    the instruction instead of what the reader asked.
    """
    plan_data = await generate_plan(client, model, question, max_tokens)

    # Build Plan object (validate structure)
    plan = Plan(
        topic=plan_data.get("topic", "Untitled"),
        question=plan_data.get("question", question),
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
            for c in plan_data.get("steps", [])
        ],
        reframed=plan_data.get("reframed", False),
        followups=plan_data.get("followups", []),
    )

    # Deterministic expansion (cannot fail validation)
    deck = expand_plan_to_deck(plan)
    COUNTERS.bump("deck.plan_expand_ok")
    LOG.info("plan-expand: deck generated for %s/%s", "provider", model)
    return deck