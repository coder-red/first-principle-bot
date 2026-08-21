"""The request body, and how it becomes a message list.

Every field is bounded. The endpoint is unauthenticated by default, so an
unbounded `history` would let anyone push an arbitrarily large body through
the model at your expense.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from fpb.prompts import DECK_SYSTEM_PROMPT


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class Focus(BaseModel):
    """A node the reader wants decomposed further.

    Drilling into a chain rung, drilling into a card, and asking a free-text
    question about a card all reduce to this: build a deck that starts here.
    """
    title: str = Field(default="", max_length=300)
    principle: str = Field(default="", max_length=2000)
    chain: List[str] = Field(default_factory=list, max_length=6)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    # build_messages only uses the last 20; the cap is well above that so a
    # long session never 422s, while the body stays bounded.
    history: List[HistoryMessage] = Field(default_factory=list, max_length=60)
    focus: Optional[Focus] = None

    @field_validator("message")
    @classmethod
    def message_has_content(cls, value: str) -> str:
        # min_length counts spaces, so without this "   " reaches the model
        # and spends a real call on a question that does not exist.
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


def focus_instruction(focus: Focus) -> str:
    lines = [
        "This is a DRILL-DOWN. Do not restart from the surface.",
        f'Take this established claim as the deck\'s starting point: "{focus.principle or focus.title}"',
    ]
    if focus.chain:
        lines.append("It was reached through this chain: " + " -> ".join(focus.chain))
    lines.append(
        "The question card must state that claim as the starting belief, and every descent "
        "card must go BELOW it — decompose what that claim itself rests on. Do not re-derive "
        "the chain above it."
    )
    return "\n".join(lines)


def build_messages(req: ChatRequest) -> List[dict]:
    messages = [{"role": "system", "content": DECK_SYSTEM_PROMPT}]
    for msg in req.history[-20:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})
    if req.focus:
        messages.append({"role": "user", "content": focus_instruction(req.focus)})
    messages.append({
        "role": "user",
        "content": "Build the deck now. Return only the JSON object with topic, question, cards and followups.",
    })
    return messages
