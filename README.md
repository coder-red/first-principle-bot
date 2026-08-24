# First Principle Bot

![Python version](https://img.shields.io/badge/Python%20version-3.10%2B-lightgrey)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)
![OpenAI SDK](https://img.shields.io/badge/OpenAI%20SDK-412991?style=flat&logo=openai&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)

A chatbot that reasons from first principles. Ask a question and it breaks the question down level by level until it reaches basic truths, then rebuilds the answer from those alone. Every claim carries a tag saying how well it is known, and a validator checks the whole chain against fixed rules before anything renders.

## Live Demo

- App: [first-principle-bot.onrender.com](https://first-principle-bot.onrender.com)
- Browsing the reviewed library is open. Asking new questions needs an access token — free-tier credits, so it stays gated.

## Author

- GitHub: [@coder-red](https://github.com/coder-red)
- Portfolio: [mhmdxc.vercel.app](https://mhmdxc.vercel.app)

## How It Works

```
your question → broken down step by step → bedrock → rebuilt answer
```

One model call returns a **deck** of cards: the question stepped down until it hits something basic, then built back up from there. Cards appear as each one finishes. Every claim is tagged:

| Tag | Meaning |
|---|---|
| `ATOMIC` | Basic truth: physical law, logical axiom, definition |
| `VERIFIED` | Confirmed, but could be otherwise |
| `CONVENTION` | Widely accepted, not proven |
| `ASSUMPTION` | Taken for granted |
| `UNKNOWN` | Not known, so the model says so instead of inventing an answer |

Conventions and assumptions get thrown out during the rebuild, so the final answer stands on atomic and verified claims alone.

## Using It

- **Ask anything.** Type a question and read the deck as it streams in.
- **Question a card.** Any card can be drilled into or asked about directly.
- **Quiz.** The deck turns into recall questions; answering schedules spaced reviews in your browser (`1/3/7/16/35` days).
- **Explore.** Sectors like physics or history come with their own question pools.
- **Library decks.** Some decks were generated ahead, passed validation, and I read them before publishing. They load instantly and carry a `Reviewed deck.` mark.

## Run It Locally

Python 3.10+ and one provider API key.

```bash
git clone https://github.com/coder-red/first-principle-bot.git
cd first-principle-bot
pip install -r requirements.txt
cp .env.example .env   # add your key
python main.py
```

Groq, Gemini, Cerebras, NVIDIA, Mistral, Together and OpenAI have their endpoints built in when you want to chain more through `PROVIDERS`; `.env.example` starts you on OpenRouter (free models, one key).

## Tech Stack

| | |
|---|---|
| **Backend** | Python 3.10+, FastAPI |
| **Model access** | OpenAI SDK against any OpenAI-compatible endpoint |
| **State** | stdlib `sqlite3`, optional |
| **Frontend** | Vanilla JS + CSS, light/dark themes, no bundler |
| **Tests** | pytest + Playwright (`python -m pytest tests/ -q`) |

## Limitations

- Answers take 25–35 seconds on free tiers.
- Validation checks the chain's form, not whether the claims are actually true. Disagreeing with a tag is often the interesting part.
- Free-tier caps apply per account per day, so heavy use runs providers dry.

