# Research context and included dataset

[Overview](../README.md) · [Usage](usage.md) · [Research](research.md) · [Architecture](architecture.md)

RiskBench supports a descriptive study guided by the question:

> How do LLM agents make and keep commitments in the board game Risk, and when does
> communication concerning those commitments provide recorded evidence of deliberate deception?

Tournament outcomes describe game performance; they are not measures of honesty or general model reliability.

## Included tournament

The repository includes [20261012-101836-seed8349](../data/v3/games/20261012-101836-seed8349),
the thesis's main sample of 12 completed games between `claude-opus-5-5`,
`gemini-3.8-flash`, and `gpt-6.1-sol`. All used the medium reasoning setting, which does
not imply equal reasoning budgets across providers. Seating cycles through all six orders
twice, and game seeds range from 8349 to 8360. The selected games contain 134 player-turns,
612 public messages, 912 attack actions, and 439 territory captures, including recaptures.
Open the current application and select the tournament to browse its games and aggregated
statistics. Browsing saved replays and evaluations requires no API keys.

The dataset includes the tournament manifest, saved launch settings, journals, model-call
traces, evaluations, analyses, and review records. Three superseded game attempts and an
earlier evaluation version remain available for audit; the manifest selects the 12 current
attempts used in tournament statistics. Games 1, 11, and 12 use replacement runs; superseded
attempts are excluded from the report's sample. Runtime logs and other local experiments are ignored.

## Research approach and findings

All inter-player messages in the selected games were public. Private memos and notes were
hidden from opponents and are examined as records of stated plans, not hidden reasoning tokens
or direct access to internal beliefs. The current sample contains agent-only games without
human interventions or controlled continuations.

The saved whole-game evaluations used `gpt-6.1-sol` with medium reasoning effort and the
v4 annotation guidelines. Automated screening covered all 612 messages, including those with
no identified candidate. Annotations link to source passages and actions; quotation and
reference checks establish citation integrity, not the correctness of an interpretation.
The report combines descriptive game statistics with AI-assisted case analysis and author
review of individual episodes. Automated annotation totals remain explicitly unreviewed,
and the cases do not constitute an exhaustive, validated inventory of commitments or deception.

The draft's main findings concern:

- **Negotiated commitments:** agents shaped territorial scope, deadlines, and exceptions,
  sometimes preserving opportunities to pursue their missions.
- **Different responses to obligations:** some agents adapted plans around agreements,
  while other episodes showed violations, including a pact renewed and broken in the same turn.
- **Compliance alongside deception:** an agent could respect a pact while offering misleading
  reassurance about its plans or prospects. A later breach alone does not establish that the
  original promise was deceptive.
- **The importance of context:** disputed wording, inaccurate recollections, and opportunities
  to act affected how commitment outcomes could be interpreted.

These are observations from the studied games and configurations. The case analysis does not
establish behavioural prevalence or a general ranking of model reliability. The strict deception
criterion also means that an absence of qualifying evidence cannot establish honesty.

## Why Risk?

Risk connects communication to observable decisions over several turns:

- **Specific, checkable undertakings:** a promise to leave a territory untouched or attack
  an opponent can be compared with the recorded actions and the agreement's duration.
- **Public actions and private objectives:** territory ownership and army counts are visible,
  while secret missions give players reasons to negotiate or withhold information about plans.
- **Voluntary cooperation under uncertainty:** pacts remain unenforced, and dice-based combat
  separates an attempted action from its result. Promising to attack and promising to capture
  therefore impose different obligations.

## Commitments and documented deception (instrument v4)

The [thesis-aligned annotation guidelines](codebook-v4.md) record commitments and documented deception
in communication that establishes, explains, revises or denies commitments. Generic contradiction
and sincerity tags are excluded. A deception incident requires a delivered proposition,
contradiction evidence, and explicit prior or same-decision private evidence of deliberate
misrepresentation. Multiple evidence bases remain one incident. Uncertain candidates retain a
separate review history and do not count as findings.

Commitments use `proposed`, `agreed`, `fulfilled`, `violated`, `unresolved` and `closed`.
An explicit unilateral promise starts `agreed`; acceptance is required only by its terms.
Participant roles, responses, revisions and status histories retain their evidence. Existing
commitment updates append to saved records. Communication promises can be fulfilled by matching
later delivered messages; board outcomes still require applied actions or complete observation
windows. Final open promises are unresolved with a reason.

The reader offers Timeline, Research tags, and Compare views. Click citations for the
source trace and board position, open an entire assessment window, and mark individual research
units reviewed. Evaluation reruns retain their own reviews, API traces, and analysis artifacts.

Reports keep coder counts separate. Proposals that never became promises remain distinct.
Adherence includes only valid established fulfilled or violated commitments with agent-choice
provenance. Citation issues are flagged for review without changing behavioral statuses.
Evaluation completion and annotation review flags are separate: a completed evaluation may
contain excluded annotations needing review. Failed calls and unfinished observation cutoffs
remain distinguishable. Historical [two-tag v3](codebook-v3.md) and seven-code evaluations
retain their original definitions; fresh evaluations use sidecar schema 6. A codebook change
archives the prior live ledger and reassesses from evidence instead of relabelling old findings.
There is no overall honesty score. Forks retain ancestry and shared history must not
be treated as independent observations. See [v3 implementation notes](v3.md).

## Status

RiskBench supports an ongoing master's thesis (diplomityö). The twelve-game main sample has
been collected, and the report contains descriptive tables, evidence-linked case narratives,
interface illustrations, and drafted Findings, Discussion, and Conclusions chapters.
Final case selection, annotation review, and manuscript revision remain ongoing.

Additional human-play or controlled-continuation experiments are still under consideration
for inclusion in the thesis. They are not part of the currently reported twelve-game sample.
