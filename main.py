import os
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from openai import AsyncOpenAI
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="First Principle Bot")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")


def make_error_flashcards(topic: str, error: str) -> dict:
    topic = topic.strip() or "your question"
    return {
        "topic": "Flashcard setup issue",
        "cards": [
            {
                "title": "I Could Not Generate This Deck",
                "imagePrompt": "warning panel with API connection and flashcard labels",
                "question": f"Why did the bot fail to answer: {topic[:80]}?",
                "principle": "[VERIFIED] The app needs a working model response before it can create topic-specific cards.",
                "explanation": error,
                "takeaway": "Fix the model/API setup, then ask again.",
            },
        ],
    }


def extract_json_object(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise

TEXT_SYSTEM_PROMPT = """You are First Principle Bot. You explain topics using genuine first-principles reasoning — the method Aristotle called "the first basis from which a thing is known" and Descartes practiced as systematic doubt. You do NOT fill out a template. You REASON OUT LOUD.

You MUST execute the following thinking protocol step by step in your response:

══ PHASE 1: IDENTIFY THE QUESTION ══
State the single question you are trying to answer. Be precise. If the user's prompt is vague, define the specific question first.

══ PHASE 2: INVENTORY OF BELIEFS ══
List everything you think you know about the topic — every common belief, convention, factoid, and assumption. Do not evaluate them yet. Just put them on the table.

══ PHASE 3: CARTESIAN DOUBT ══
For each belief from Phase 2, apply systematic doubt:
- Can I prove this is true?
- What would it take to falsify this?
- Is this a physical necessity, a logical necessity, or a human convention?
- Could this be false and everything else still work?

Label each surviving belief as one of:
[ATOMIC] — cannot be reduced further; a true first principle (physical law, logical axiom, definitional truth)
[VERIFIED] — empirically confirmed, but could in principle be otherwise
[CONVENTION] — widely accepted but not proven; discard during reconstruction
[ASSUMPTION] — taken for granted; discard during reconstruction
[UNKNOWN] — not known; stop here

══ PHASE 4: RECURSIVE DECOMPOSITION (THE CHAIN) ══
Take every [VERIFIED] and [CONVENTION] item and decompose it:
- Ask "What is this made of?" (constituents)
- Ask "What is this based on?" (foundations)
- Ask "What must be true for this to exist?" (prerequisites)

If the answer is still decomposable, decompose again. Show the chain with indentation:
Level 1: claim → Level 2: what it's made of → Level 3: what THAT is made of → ... until you hit [ATOMIC].
You must go at least 3 levels deep on at least one branch. If you can go deeper, go deeper.

══ PHASE 5: DISCARD ══
Remove every [CONVENTION], [ASSUMPTION], and [UNKNOWN] item. Keep ONLY [ATOMIC] and [VERIFIED] items. These are your first principles.

══ PHASE 6: RECONSTRUCTION ══
Using ONLY your surviving first principles ([ATOMIC] and [VERIFIED]), rebuild the explanation from the ground up. Each step must follow logically from the last. If at any point you need to reintroduce a [CONVENTION] to make the explanation work, flag it explicitly: "WARNING: this step relies on the convention that X, not on a first principle."

══ PHASE 7: INSIGHT ══
State clearly what becomes visible from this perspective that analogical/conventional thinking would miss. What did discarding the conventions reveal?

══ STRICT RULES ══
1. ZERO analogies. Never say "it's like" or "similar to" as explanation. Forbidden.
2. You must show your decomposition chain (Phase 4) indented, at least 3 levels deep.
3. Reconstruction (Phase 6) may ONLY use [ATOMIC] and [VERIFIED] items. If you use a [CONVENTION], you must flag it with a WARNING.
4. If you don't know something, say "I don't know" — do not fabricate.
5. Distinguish physical necessity from logical necessity from human convention.
6. A curious 16-year-old must be able to follow every step."""

FLASHCARD_SYSTEM_PROMPT = """You are First Principle Bot in FLASHCARD mode. Generate a short deck of proper flashcards that teaches the user's topic from first principles.

Return ONLY valid JSON. No Markdown, no code fences, no extra text.

JSON shape:
{
  "topic": "short topic title",
  "cards": [
    {
      "title": "short card title",
      "imagePrompt": "concrete visual description for a simple educational illustration",
      "question": "one clear question this card answers",
      "principle": "one [ATOMIC] or [VERIFIED] first principle",
      "explanation": "2-4 short sentences that explain the idea from the principle",
      "takeaway": "one concise memory hook"
    }
  ]
}

Create 4-6 cards. Each card must teach one step, and the deck must progress from foundations to insight.

STRICT RULES:
1. ZERO analogies. Never say "it's like" or "similar to".
2. Use only [ATOMIC] truths and [VERIFIED] facts as principles.
3. If you don't know something, say "I don't know" in the relevant card.
4. Keep each explanation easy for a curious 16-year-old.
5. imagePrompt must describe visible objects, labels, or physical structures from the card topic."""

class ChatRequest(BaseModel):
    message: str
    history: List[dict] = []
    mode: str = "text"

@app.post("/api/chat")
async def chat(req: ChatRequest):
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        if req.mode == "flashcard":
            error = "OPENROUTER_API_KEY is not set for this server process. Create a .env file in the project or start the server from a shell where the key is available."
            return StreamingResponse(iter([json.dumps(make_error_flashcards(req.message, error))]), media_type="text/plain")
        raise HTTPException(status_code=400, detail="API key is required. Set OPENROUTER_API_KEY or OPENAI_API_KEY environment variable.")

    api_endpoint = os.environ.get("API_ENDPOINT", "https://openrouter.ai/api/v1")
    model = os.environ.get("MODEL", "openrouter/free")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=api_endpoint,
        default_headers={
            "HTTP-Referer": "http://127.0.0.1:8000",
            "X-Title": "First Principle Bot",
        },
    )

    system_prompt = FLASHCARD_SYSTEM_PROMPT if req.mode == "flashcard" else TEXT_SYSTEM_PROMPT

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history[-20:]:  # keep context manageable
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": req.message})

    if req.mode == "flashcard":
        messages.append({
            "role": "user",
            "content": "Generate the flashcard deck now. Return only the JSON object with topic and cards.",
        })
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                temperature=0.4,
                max_tokens=2500,
            )
            content = response.choices[0].message.content if response.choices else ""
        except Exception as exc:
            error = f"Model provider error: {str(exc)}"
            return StreamingResponse(iter([json.dumps(make_error_flashcards(req.message, error))]), media_type="text/plain")

        if content and content.strip():
            try:
                parsed = extract_json_object(content)
                if isinstance(parsed.get("cards"), list) and parsed["cards"]:
                    return StreamingResponse(iter([json.dumps(parsed)]), media_type="text/plain")
            except (json.JSONDecodeError, AttributeError):
                error = "The model replied, but it did not return valid flashcard JSON. Raw response: " + content[:700]
                return StreamingResponse(iter([json.dumps(make_error_flashcards(req.message, error))]), media_type="text/plain")

        error = "The model returned an empty message. Check that MODEL points to a chat model that supports completions."
        return StreamingResponse(iter([json.dumps(make_error_flashcards(req.message, error))]), media_type="text/plain")

    async def generate():
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                temperature=0.7,
                max_tokens=2000
            )
            saw_content = False
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    saw_content = True
                    yield delta.content

            if not saw_content:
                yield "I did not receive text from the model. Try again, or switch models in your .env file."
        except Exception as exc:
            yield f"Error from model provider: {str(exc)}"

    return StreamingResponse(generate(), media_type="text/plain")

@app.get("/api/health")
async def health():
    key_set = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    return {
        "status": "ok",
        "api_key_configured": key_set,
        "api_endpoint": os.environ.get("API_ENDPOINT", "https://openrouter.ai/api/v1"),
        "model": os.environ.get("MODEL", "openrouter/free"),
    }


@app.get("/api/sample-flashcards")
async def sample_flashcards():
    return {
        "topic": "Why the sky is blue",
        "cards": [
            {
                "title": "Sunlight Contains Many Wavelengths",
                "imagePrompt": "sunlight split into labeled red green blue violet wavelengths",
                "question": "What reaches Earth's atmosphere from the Sun?",
                "principle": "[VERIFIED] Sunlight contains visible electromagnetic waves with different wavelengths.",
                "explanation": "Visible sunlight is not one wavelength. Measurements with prisms and spectrometers show a range from shorter violet and blue wavelengths to longer red wavelengths.",
                "takeaway": "Different wavelengths enter the air together.",
            },
            {
                "title": "Air Molecules Scatter Light",
                "imagePrompt": "tiny nitrogen and oxygen molecules scattering blue rays in the atmosphere",
                "question": "What does air do to incoming light?",
                "principle": "[VERIFIED] Gas molecules can redirect incoming electromagnetic waves.",
                "explanation": "Earth's atmosphere contains small gas molecules. When sunlight passes through them, some light is redirected away from its original path and moves toward your eyes from different parts of the sky.",
                "takeaway": "The sky glows because air redirects light.",
            },
            {
                "title": "Short Wavelengths Scatter More",
                "imagePrompt": "blue light rays scattered strongly while red rays travel straighter",
                "question": "Why is the scattered light mostly blue?",
                "principle": "[VERIFIED] Rayleigh scattering is stronger for shorter visible wavelengths.",
                "explanation": "Blue light has a shorter wavelength than red light. In clean air, shorter visible wavelengths are redirected more strongly, so more blue light reaches your eyes from the open sky.",
                "takeaway": "Blue gets redirected more than red.",
            },
            {
                "title": "Your Eyes Complete The Perception",
                "imagePrompt": "human eye receiving scattered blue light from the daytime sky",
                "question": "Why do we perceive blue instead of violet?",
                "principle": "[VERIFIED] Human color vision depends on cone sensitivity and the light that reaches the retina.",
                "explanation": "Violet is also scattered strongly, but there is less violet in sunlight, some is absorbed higher in the atmosphere, and human eyes are less sensitive to it. The combined signal is perceived as blue.",
                "takeaway": "The physical light and your eye both matter.",
            },
        ],
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
