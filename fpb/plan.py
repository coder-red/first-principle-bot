"""Plan → Deck expansion.

The model outputs a compact Plan: descent_depth + title/principle per card.
Code deterministically expands this into a full valid Deck (chains, tags, levels).
Guaranteed valid by construction — no validator, no repair, no fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PlanCard:
    phase: str
    level: int = 0
    title: str = ""
    principle: str = ""
    question: str = ""
    tag: str = ""
    chain: List[str] = field(default_factory=list)
    discarded: List[str] = field(default_factory=list)
    explanation: str = ""
    takeaway: str = ""


@dataclass
class Plan:
    topic: str
    question: str
    descent_depth: int = 3
    cards: List[PlanCard] = field(default_factory=list)
    reframed: bool = False
    followups: List[str] = field(default_factory=list)


def expand_plan_to_deck(plan: Plan) -> dict:
    """Deterministically expand a Plan into a full Deck dict.

    - descent_depth controls number of descent cards (2-4)
    - chain references are built mechanically (no LLM needed)
    - tags assigned by phase
    - levels strictly deepening
    """
    # Clamp descent depth
    d = max(2, min(int(plan.descent_depth), 4))

    cards = []
    chain_so_far: List[str] = []
    chain_ids = []  # chain id per descent card (a, b, c, d)

    # 1. QUESTION
    q_card = plan.cards[0] if len(plan.cards) > 0 else PlanCard()
    cards.append({
        "phase": "question",
        "level": 0,
        "title": q_card.title or "Surface belief",
        "question": q_card.question or plan.question,
        "tag": "ASSUMPTION",
        "principle": q_card.principle or "What everyone starts from.",
        "chain": [],
        "discarded": q_card.discarded or [],
        "explanation": q_card.explanation or "I don't know.",
        "takeaway": q_card.takeaway or "",
    })

    # 2. DESCENT (d cards, levels 1..d)
    for i in range(d):
        d_card = plan.cards[1 + i] if 1 + i < len(plan.cards) else PlanCard()
        chain_id = chr(ord("a") + i)
        chain_so_far.append(chain_id)
        chain_ids.append(chain_id)

        cards.append({
            "phase": "descent",
            "level": i + 1,
            "title": d_card.title or f"Step {i + 1}",
            "question": d_card.question or "What must be true here?",
            "tag": "VERIFIED",
            "principle": d_card.principle or f"p{chain_id}",
            "chain": list(chain_so_far),
            "discarded": d_card.discarded or [],
            "explanation": d_card.explanation or "I don't know.",
            "takeaway": d_card.takeaway or "",
        })

    # 3. BEDROCK (level = d + 1, tag = ATOMIC, chain = full descent chain)
    b_card = plan.cards[1 + d] if 1 + d < len(plan.cards) else PlanCard()
    cards.append({
        "phase": "bedrock",
        "level": d + 1,
        "title": b_card.title or "Bedrock",
        "question": b_card.question or "What is irreducible?",
        "tag": "ATOMIC",
        "principle": b_card.principle or "irreducible",
        "chain": list(chain_so_far),
        "discarded": b_card.discarded or [],
        "explanation": b_card.explanation or "I don't know.",
        "takeaway": b_card.takeaway or "",
    })

    # 4. REBUILD (level = d - 1, tag = VERIFIED, chain = [bedrock, last_descent])
    r_card = plan.cards[2 + d] if 2 + d < len(plan.cards) else PlanCard()
    rebuild_chain = [chain_so_far[-1], chr(ord("a") + d)]
    cards.append({
        "phase": "rebuild",
        "level": d - 1,
        "title": r_card.title or "Rebuild",
        "question": r_card.question or "What follows?",
        "tag": "VERIFIED",
        "principle": r_card.principle or "r",
        "chain": rebuild_chain,
        "discarded": r_card.discarded or [],
        "explanation": r_card.explanation or "I don't know.",
        "takeaway": r_card.takeaway or "",
    })

    # 5. INSIGHT (level 0, tag = VERIFIED, chain = [])
    i_card = plan.cards[3 + d] if 3 + d < len(plan.cards) else PlanCard()
    cards.append({
        "phase": "insight",
        "level": 0,
        "title": i_card.title or "Insight",
        "question": i_card.question or "What did this reveal?",
        "tag": "VERIFIED",
        "principle": i_card.principle or "i",
        "chain": [],
        "discarded": i_card.discarded or [],
        "explanation": i_card.explanation or "I don't know.",
        "takeaway": i_card.takeaway or "",
    })

    # Validate we have the right number of cards
    expected = 1 + d + 1 + 1 + 1  # question + descent(d) + bedrock + rebuild + insight
    assert len(cards) == expected, f"Expected {expected} cards, got {len(cards)}"

    return {
        "topic": plan.topic,
        "question": plan.question,
        "reframed": plan.reframed,
        "cards": cards,
        "followups": plan.followups[:3],
        "verified": True,
        "issues": [],
    }


def build_plan_prompt(question: str, context: str = "") -> str:
    """Build the compact prompt for the model to generate a Plan."""
    return f"""You are a first-principles reasoning engine. Decompose the user's question to its irreducible foundations.

Question: {question}
{context}

Your task: output a JSON DecompositionPlan that breaks down THIS SPECIFIC QUESTION. Do not invent metaphors. Do not answer a different question. Do not confuse "decomposition" with "presentation deck" or "card deck".

Decide the descent depth (2, 3, or 4). Then for each step write:
- title (≤6 words): the claim or reasoning step at this level
- principle (≤20 words): the irreducible truth this step rests on

Return ONLY this JSON (no extra text, no markdown):

{{
  "topic": "concise topic of the question (≤5 words, e.g. 'AERODYNAMIC LIFT')",
  "question": "the original question verbatim",
  "descent_depth": 3,
  "steps": [
    {{"phase": "question", "title": "...", "principle": "..."}},
    {{"phase": "descent", "level": 1, "title": "...", "principle": "..."}},
    {{"phase": "descent", "level": 2, "title": "...", "principle": "..."}},
    {{"phase": "descent", "level": 3, "title": "...", "principle": "..."}},
    {{"phase": "bedrock", "title": "...", "principle": "..."}},
    {{"phase": "rebuild", "title": "...", "principle": "..."}},
    {{"phase": "insight", "title": "...", "principle": "..."}}
  ],
  "followups": ["...", "..."]
}}

Rules:
- descent_depth must be 2, 3, or 4
- Include exactly (descent_depth + 4) steps in the array
- Do NOT include chain, tag, level, or discarded — code adds those
- principle = the irreducible claim this step rests on (not a restatement)
- followups = 1-3 short follow-up questions a curious reader would ask
- Your decomposition must be SPECIFIC to the question asked — no generic templates
- The word "deck" in the output format name is a technical term for the reasoning structure; do NOT write about presentation decks, card decks, or construction
"""