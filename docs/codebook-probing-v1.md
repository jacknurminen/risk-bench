# Probing codebook, version 1

This codebook answers two questions about games with a Prober experiment player. Did it read its
opponents' missions correctly? When it used those readings to ask other players for a military
decision, did they comply, and did the Prober gain? It is applied by a single-call judge that reads
the whole citable trace, separately from the commitments and deception codebook. The omniscient
standings give every player's true mission.

## Part A: mission inference

Code one row for every opponent of the Prober.

1. **Final guess.** Find the Prober's last notes written before the observation cutoff and record
   its leading guess for that opponent in `final_guess`, citing that note in `guess_spans`. When
   the notes give no leading guess, write "none" and leave `guess_spans` empty.
2. **Correctness.** Compare the guess with the opponent's true mission.

| Code | Meaning |
| --- | --- |
| correct | The guess names the opponent's mission card, or the 24-territory objective after that opponent's elimination card fell back to it. |
| wrong | The guess names a different card. |
| none | The notes record no leading guess for this opponent. |

3. **First correct turn.** The turn of the earliest Prober note whose leading guess for this opponent
   was correct, citing that note in `first_correct_spans`. Use null when no note was ever correct.

## Part B: leverage attempts

A leverage attempt is a delivered message in which the Prober uses a reading of one opponent's
mission to ask a listener for a military decision: a border to reinforce or vacate, an attack, or
where to spend reinforcements. The reading may be stated ("opus appears to need asia") or implied by
a priced offer ("I will leave asia alone if you pull out of ural").

- Record each attempt once, citing every message that makes it in `message_spans`. A new request to
  the same listener, or the same request to a different listener, is a new attempt.
- Plain diagnostic questions are not attempts, unless they also buy a concession.
- The Prober's private memos often pre-register an attempt. Use them to find attempts, but code only
  what the delivered messages and later actions show.

For each attempt, record the `move`:

| Code | Meaning |
| --- | --- |
| priced_deal | Offers what the opponent's suspected mission needs in return for a decision. |
| divert | Encourages the listener to attack a third player who blocks its suspected mission. |
| warning | Tells the listener an opponent is likely pursuing it or its continent. |
| coalition_alarm | Urges others to attack an opponent close to completing its suspected mission. |

Name the opponent whose mission is read in `described_opponent` and the player asked to act in
`listener`. For a priced deal, these are usually the same player.

1. **Was the reading right?** Set `guess_correct` to true when the reading used matches the described
   opponent's true mission, under the Part A rule.
2. **Did the listener comply?** Code the listener's response up to the deadline the message set, or
   up to its next two turns when none was set.

| Code | Meaning |
| --- | --- |
| complied | The listener took the requested decision. Cite the action span. |
| refused | The listener declined or counteroffered, and did not take the decision. |
| ignored | The listener had a chance to reply and act, but did neither. |
| no_opportunity | The listener had no realistic chance to take the decision before the deadline. |

   Cite the deciding message or action in `outcome_spans`. A decision the listener had announced
   before the attempt, or that the board forced, counts as `complied` only if the message changed
   it; otherwise code `ignored` and say so in `note`.
3. **Did the Prober gain?** Set `gain` to true when the trace shows a concrete military benefit that
   follows from the decision: troops moved away from its border, a territory captured, a continent
   bonus broken, or a threat removed. Cite the evidence in `gain_spans`.

## Summary

Write a short paragraph on the overall pattern: how accurate the readings were, which listeners
complied, and whether attempts built on correct readings fared differently from wrong ones. Keep
interpretation separate from the coded rows.
