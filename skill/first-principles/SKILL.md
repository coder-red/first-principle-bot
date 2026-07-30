---
name: first-principles
description: Use when someone wants something decomposed to what is irreducibly true and rebuilt from there - "explain from first principles", "what is X really", "why does X actually work", challenging a textbook explanation, or auditing whether a belief rests on evidence or on convention. Also use before committing to a design premise that everyone treats as given. Do NOT use for quick factual lookups, for summarising, or when an analogy is genuinely the best way to teach the thing.
---

# First principles

Most explanation is reasoning by analogy: this new thing resembles that
familiar thing, so borrow the familiar thing's structure. That is fast and
usually good enough, and it silently inherits every assumption baked into the
familiar thing.

This skill does the opposite. It takes a claim apart until what remains cannot
be taken apart further, throws away everything that turned out to be convention,
and rebuilds the explanation using only what survived.

## When to fire, and when not to

Fire when the value is in the *foundation*:

- "Explain X from first principles" / "what is X really"
- The received explanation is suspected to be wrong or shallow
- A decision rests on a premise nobody has examined
- Someone is stuck because they inherited a constraint that may not be real

Do not fire when:

- A plain factual answer is what is wanted. Decomposing "what year did X ship"
  is theatre.
- An analogy genuinely teaches better. This skill bans analogy, which is a
  feature for foundations and a liability for intuition-building. Say so and
  answer normally.
- The user needs speed. This is a slow, deliberate method.

If unsure, ask whether they want the short answer or the decomposition.

## The five tags

Every claim gets exactly one. The tags are the whole point: they force the
distinction between *proven*, *observed*, and *merely agreed*.

| Tag | Meaning | Test |
| --- | --- | --- |
| `ATOMIC` | Irreducible | A physical law, logical axiom, or definitional truth. Asking "what is this made of?" produces nothing further. |
| `VERIFIED` | Empirically confirmed | Measurable and confirmed, but the universe could have been otherwise. |
| `CONVENTION` | Widely agreed, unproven | Everyone does it this way. Nothing breaks if they stopped. |
| `ASSUMPTION` | Taken for granted | Never examined. Often invisible until named. |
| `UNKNOWN` | Not known | Stop here. Do not fabricate a floor. |

`CONVENTION` and `ASSUMPTION` are discarded before rebuilding. That discarding
is where the insight comes from.

## The protocol

Work through this before writing anything. Phases 1-5 are thinking; the user
sees phases 6-7 plus the chain.

1. **Identify the question.** One precise question. If the prompt is vague,
   define the specific question first and say that you have.
2. **Inventory beliefs.** Everything you think you know. Do not evaluate yet.
3. **Apply doubt.** For each: Can I prove it? What would falsify it? Is this
   physical necessity, logical necessity, or human convention? Could it be false
   with everything else still standing? Tag each one.
4. **Decompose recursively.** Take the surviving claim and ask "what is this
   made of?", "what is this based on?", "what must be true for this to exist?"
   Repeat until `ATOMIC`. **Go at least three levels.** If you can go deeper, go
   deeper.
5. **Discard.** Drop every `CONVENTION`, `ASSUMPTION` and `UNKNOWN`.
6. **Rebuild.** Reconstruct using only `ATOMIC` and `VERIFIED` material. If a
   step needs a convention to work, flag it in the open: "this step relies on
   the convention that X."
7. **State the insight.** What is visible now that the conventions are gone,
   that analogical thinking would have missed?

## Check your own chain before answering

A decomposition that breaks its own rules is worse than no decomposition,
because the form signals rigour that the content does not have. Verify all of
these and fix any that fail:

- Exactly one bedrock claim.
- The descent goes strictly deeper at each step, starting one level below the
  surface belief.
- The bedrock lies **below** the last descent step. If they are at the same
  depth, the descent did not actually reach bedrock.
- The bedrock is tagged `ATOMIC`, or `UNKNOWN` if it honestly cannot reduce
  further. Never invent a floor to look complete.
- **No descent step is tagged `ATOMIC`.** If a step were irreducible, the
  decomposition should have stopped there and that step is the bedrock. This is
  the most common failure.
- The rebuild leans only on `ATOMIC` and `VERIFIED` material, or names the
  convention it borrows.
- Each step's chain extends the previous step's chain rather than restarting.

If you cannot satisfy these, say which one failed and why. An honest "I could
not reduce this past X" is worth more than a tidy chain that cheats.

## Hard rules

1. **Zero analogies.** Never "it's like", "similar to", "think of it as". If you
   catch yourself reaching for one, you have not decomposed far enough.
2. **Never fabricate a floor.** `UNKNOWN` is a legitimate bedrock.
3. **Distinguish necessity from convention.** "Must be so" and "we all do it
   this way" are different claims. Conflating them is the failure this method
   exists to prevent.
4. **A curious sixteen-year-old must be able to follow every step.**

## Output

**Default: prose.** Lead with the question, show the chain as an indented
ladder, mark each rung with its tag, then rebuild and state the insight. Keep
the ladder rungs short - they are signposts, not sentences.

**On request, or when the chain is worth exploring: a deck.** Render it as a
self-contained HTML artifact using `assets/deck.html`. Replace the `DECK`
constant with your deck object and publish. The schema is in
`references/deck-schema.md`. The artifact needs no network access, no build
step, and no server.

Offer the deck when the chain is four or more levels deep, or when the user is
likely to want to walk it rather than read it. Do not offer it for a two-step
decomposition; a card deck for that is ceremony.
