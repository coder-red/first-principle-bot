# Deck schema

A deck is a rendering of **one** decomposition chain. It is not a list of facts
about the topic. The reader descends until they hit bedrock, then climbs back up
rebuilding the explanation from that bedrock alone.

```json
{
  "topic": "short title",
  "question": "the single precise question this deck answers",
  "cards": [
    {
      "phase": "question | descent | bedrock | rebuild | insight",
      "level": 0,
      "title": "short card title",
      "question": "the one question this card answers",
      "tag": "ATOMIC | VERIFIED | CONVENTION | ASSUMPTION | UNKNOWN",
      "principle": "the single claim this card establishes, in one sentence",
      "chain": ["level 1 claim", "level 2 what it rests on", "level 3 ..."],
      "discarded": ["a convention or assumption dropped at this step"],
      "explanation": "2-4 short sentences justifying this step",
      "takeaway": "one concise memory hook"
    }
  ],
  "followups": ["a genuinely curious next question", "another"],
  "reframed": false
}
```

`reframed` is true **only** when `question` asks something materially different
from what the user typed — you narrowed a vague prompt, corrected a false
premise, or replaced the surface question with the one that must be answered
first. Rewording, expanding or formalising the same question is not reframing;
set it false however different the wording looks. When in doubt, false.

Only you can judge this. Word overlap cannot: "how does a magnet pull on
something it never touches" and "how does a magnet exert force across empty
space" share no significant words yet ask the same thing, while "why is glass
transparent" and "what must be true of a material for light to pass through it"
also share none and do not.

## Card order

Six or seven cards, in this order:

1. **One `question` card, level 0.** States the precise question and the surface
   belief most people start from. Tag it `CONVENTION` or `ASSUMPTION` - the
   starting point is almost never a first principle. Put the beliefs you are
   about to strip away in `discarded`.

2. **Three or more `descent` cards, levels 1, 2, 3...** one level per card,
   strictly increasing. Each answers "what is *this* made of?" about the card
   before it. Each must go deeper, not restate.

3. **One `bedrock` card**, at a level **below** the deepest descent card. Tag
   `ATOMIC`, or `UNKNOWN` if it genuinely cannot reduce further. This is where
   decomposition stops.

4. **One or two `rebuild` cards**, levels counting back down toward 1.
   Reconstruct using only `ATOMIC` and `VERIFIED` material. If a step needs a
   convention, say so in `explanation` and tag that card `CONVENTION`.

5. **One `insight` card, level 0.** What is visible now that the conventions are
   gone.

## Field notes

- **`level`** is depth in the chain, not card order. The renderer places each
  card at its level, so the progress rail dips to bedrock and climbs back out.
  Getting levels wrong makes the rail lie.
- **`chain`** is the path from level 1 down to *this* card, one short string per
  level, deepest last. Each card's chain must **extend** the previous card's
  chain - same entries, plus one. Keep each rung under 60 characters; they
  render as a ladder, not prose.
- **`discarded`** is what you dropped *at that step*, and renders struck
  through. Leave it empty rather than padding it.
- **`tag`** is per claim, not per card mood. Be honest: most descent steps are
  `VERIFIED`, and tagging them `ATOMIC` is the single most common error.
- **`followups`** must be answerable by the same method and genuinely
  interesting. Never restate the deck's own question.

## Worked example

The shape of a sound deck, abridged - note that levels increase strictly, the
bedrock sits below them all, and each chain extends the last:

| phase | level | tag | title |
| --- | --- | --- | --- |
| question | 0 | `ASSUMPTION` | The sky is blue |
| descent | 1 | `VERIFIED` | Colour is light reaching an eye |
| descent | 2 | `VERIFIED` | Air redirects light |
| descent | 3 | `VERIFIED` | Redirection depends on wavelength |
| bedrock | 4 | `ATOMIC` | Charges radiate when driven |
| rebuild | 2 | `VERIFIED` | The sky, rebuilt from bedrock |
| insight | 0 | `VERIFIED` | Why blue and not violet |

The insight card is the payoff: violet scatters more than blue, so pure physics
predicts a violet sky. The answer depends on the observer's eye as much as on
the air - which the surface story hides entirely.
