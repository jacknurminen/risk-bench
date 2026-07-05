# Academic Writing Style Guidelines

These rules govern **prose written for the report**: `docs/report/thesis/thesis.tex`, the
`docs/report/thesis-md/` chapters, and the `docs/report/notes/` when a note will be paraphrased into
the thesis. They do **not** apply to code, commit messages, or the development log.

Two goals reinforce each other here: prose should read in a proper academic register, and it should
**not** carry the statistical tells of language-model output. A thesis examiner in 2026 knows those
tells; writing that trips them invites doubt about authorship regardless of the work's quality.

The drafted chapters (`03-methods.md`, `05-discussion.md`) are the reference for voice. Match them.

## Register and voice

- **MUST** write in a formal, precise, declarative academic register. The thesis is impersonal:
  "this thesis", "the study", "the author" (for the coding work); never "I", and do not slip into a
  chatty or explanatory-blog tone.
- **MUST** make every claim proportional to its evidence, and cite where the thesis requires it.
  Hedge with content, not filler: "the behaviour appears in two of three games", not "it may
  potentially be the case in some instances".
- **PREFER** the specific to the general: name the model, the turn, the family code, the number.
- **MUST** vary sentence length. Follow a long, subordinated sentence with a short one to control
  rhythm. Uniform medium-length sentences are themselves a tell of generated text.
- When editing existing prose, **preserve the author's voice** and remove only the tells; do not
  rewrite passable academic writing into a different style.

## Punctuation: ration the em dash — and the colon

Em dashes are permitted but **rationed** — used where they do work a comma cannot, not as a reflex.

- **AVOID** the paired em dashes that wrap a dramatic aside. Prefer a full stop, a comma, parentheses,
  or a semicolon. Keep to roughly **one em dash per paragraph** at most.
  - Before: `the ground it offers has a boundary — the thought layer is itself generated text — a rationale`
  - After: `the ground it offers has a boundary. The thought layer is itself generated text, a rationale`
- **NEVER** use an em dash as a mechanical pause generator where a comma or period is correct.
- **PREFER** the semicolon and colon where they are the better academic choice: a semicolon to join
  related independent clauses, a colon to introduce an elaboration or list.
- **RATION the pivot colon the same way.** A colon that introduces a list, a definition, or a term
  gloss is conventional and free; a full sentence turning on `claim: elaboration` is a rhythm
  device and gets the same budget as the em dash — roughly **one per paragraph**. Do not fix an
  em-dash overuse by minting colons; recast by varied means instead: split into two sentences,
  subordinate with *because* / *so that* / *since*, or fold the elaboration into the clause.
  Three colon-pivot sentences in a row read as mechanical as three dashes.

## Vocabulary to avoid (the LLM cluster)

- **NEVER** use the words the models over-produce: *delve, tapestry, realm, landscape (as metaphor),
  underscore, showcase, boast, leverage (as a verb), seamless, robust (as filler), pivotal, crucial
  and vital (as filler), foster, unlock, harness (as metaphor), myriad, plethora, testament to,
  ever-evolving, navigate the complexities.*
- **MUST** replace them with plain, exact words: "use" not "leverage"; "shows" or "supports" not
  "underscores"; "many" not "a myriad of"; "central" not "pivotal".
- **AVOID** intensifier inflation: *very, extremely, incredibly, truly, highly, significantly* (unless
  reporting statistical significance) as reflexive boosters. Cut them or replace with a precise degree.

## Structures to avoid (the mechanical patterns)

- **NEVER** use the "not X, but Y" / "it is not just X, it is Y" antithesis as a reflex.
- **AVOID** reflexive tricolons (the rule of three). Three parallel items are fine when there are
  genuinely three; do not manufacture a third for rhythm.
- **NEVER** open a sentence with throat-clearing: *It is important to note that, It is worth noting,
  Notably, Interestingly, Importantly, Crucially.* State the point directly.
- **AVOID** formulaic scaffolding: an opening that restates the question, a body of bulleted
  bold-lead-ins, and an *In conclusion / In summary / Ultimately / Overall* wrap-up that adds nothing.
  A section does its work through its content; do not summarise a short subsection back to the reader.
- **NEVER** end a paragraph with an empty summary sentence ("Ultimately, the answer depends on the
  context.").
- **AVOID** over-signposting with a transition on every paragraph (*Moreover, Furthermore,
  Additionally, First… Second…*). Use a connective only where the logic needs one.
- **AVOID** bolding terms for emphasis inside running prose. In LaTeX use `\emph{}` sparingly, and
  reserve italics for introduced terms and genuine emphasis, as the drafted chapters do.

## Self-check before finalising a passage

Read the passage back and ask:

- Would a careful human researcher write this sentence, or does it read as generated?
- Any word from the avoid-list, or an inflated intensifier? Replace it.
- More than one em dash in the paragraph? Recast some.
- Any "not X but Y", a manufactured triple, or a throat-clearing opener? Cut it.
- Is every claim proportional to the evidence, and cited where the thesis requires?

Any single tell is weak on its own; the harm is in the cluster. The aim is prose that reads as the
considered work of the author, with the model's fingerprints removed.