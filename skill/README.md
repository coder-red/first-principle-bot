# first-principles skill

The reasoning method from this app, packaged so Claude (or any agent that reads
skill files) can use it without the server.

```
first-principles/
  SKILL.md                    the protocol, the tag vocabulary, the self-check
  references/deck-schema.md   the deck JSON contract
  assets/deck.html            self-contained artifact template
```

## Install

**Claude Code** — copy the folder into either location:

```bash
# just for this project
mkdir -p .claude/skills && cp -r skill/first-principles .claude/skills/

# for every project
cp -r skill/first-principles ~/.claude/skills/
```

Then start a new session and ask something like "explain money from first
principles". Claude reads `SKILL.md` and follows the protocol.

**Anything else** — `SKILL.md` is plain Markdown with YAML frontmatter. Paste it
as a system prompt, drop it into a custom GPT, or hand it to any agent runtime
that loads instruction files.

## What it does and does not carry

**Carries:** the seven-phase protocol, the five epistemic tags, the ban on
analogy, and — importantly — the self-check rules that catch a chain breaking
its own contract. That last part is what the server-side validator enforces in
`main.py`; the skill asks the model to enforce it on itself.

**Does not carry:** anything that needs the server. The skill makes no network
calls and depends on no API. The deck template is a single HTML file with inline
CSS and JS, which is exactly what the Artifacts sandbox requires.

## Scoping

The skill deliberately refuses to fire on quick factual lookups, and says so
when an analogy would genuinely teach better. The analogy ban is right for
building foundations and wrong for building intuition; a skill that fires
everywhere would do real damage to ordinary explanations.

If you widen the trigger, keep that carve-out.
