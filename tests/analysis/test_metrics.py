from typing import Any

from risk_bench.analysis import metrics


def _evidence(span: int, quote: str | None, resolved: bool, quote_matched: bool | None) -> dict[str, Any]:
    return {"span": span, "quote": quote, "resolved": resolved, "quote_matched": quote_matched}


def _member(index: int, tags: list[dict[str, Any]], syntheses: list[dict[str, Any]]) -> dict[str, Any]:
    return {"index": index, "tags": tags, "syntheses": syntheses}


def test_integrity_of_evidence_counts_broken_and_mismatched_quotes():
    evidence = [
        _evidence(span=1, quote="ok", resolved=True, quote_matched=True),
        _evidence(span=2, quote="bad", resolved=True, quote_matched=False),
        _evidence(span=99, quote=None, resolved=False, quote_matched=None),
        _evidence(span=3, quote=None, resolved=True, quote_matched=None),
    ]

    counts = metrics.integrity_of_evidence(evidence=evidence)

    assert counts == {
        "total_citations": 4,
        "broken_citations": 1,  # the unresolved span
        "quoted_citations": 2,  # the two with a quote
        "quote_mismatches": 1,  # the quote that is not a substring
    }


def test_integrity_sums_tags_and_synthesis_evidence_across_members():
    member = _member(
        index=0,
        tags=[
            {
                "player": "a",
                "behavior": "deception",
                "turn": 1,
                "evidence": [
                    _evidence(span=1, quote=None, resolved=True, quote_matched=None),
                    _evidence(span=9, quote=None, resolved=False, quote_matched=None),
                ],
            }
        ],
        syntheses=[
            {
                "player": "a",
                "profile": [
                    {
                        "dimension": "honesty",
                        "characterization": "x",
                        "evidence": [
                            _evidence(span=2, quote=None, resolved=True, quote_matched=None),
                        ],
                    }
                ],
                "key_episodes": [_evidence(span=3, quote=None, resolved=True, quote_matched=None)],
            }
        ],
    )

    counts = metrics.integrity(members=[member])

    assert counts["total_citations"] == 4  # 2 tag + 1 profile + 1 key episode
    assert counts["broken_citations"] == 1


def test_source_extractors_split_evidence_by_citation_site():
    member = _member(
        index=0,
        tags=[
            {
                "player": "a",
                "behavior": "deception",
                "turn": 1,
                "evidence": [
                    _evidence(span=1, quote=None, resolved=True, quote_matched=None),
                ],
            }
        ],
        syntheses=[
            {
                "player": "a",
                "profile": [
                    {
                        "dimension": "d",
                        "characterization": "c",
                        "evidence": [
                            _evidence(span=2, quote=None, resolved=True, quote_matched=None),
                        ],
                    }
                ],
                "key_episodes": [_evidence(span=3, quote=None, resolved=True, quote_matched=None)],
            }
        ],
    )

    assert [c["span"] for c in metrics.tag_evidence(member=member)] == [1]
    assert [c["span"] for c in metrics.synthesis_profile_evidence(member=member)] == [2]
    assert [c["span"] for c in metrics.key_episode_evidence(member=member)] == [3]
    assert [c["span"] for c in metrics.evidence_of_member(member=member)] == [1, 2, 3]


def _tag(player: str, behavior: str, turn: int, spans: list[int]) -> dict[str, Any]:
    evidence = [_evidence(span=span, quote=None, resolved=True, quote_matched=None) for span in spans]
    return {"player": player, "behavior": behavior, "turn": turn, "evidence": evidence}


def test_tag_agreement_groups_flaggers_and_orders_by_turn_then_player():
    members = [
        _member(
            index=0, tags=[_tag("bob", "betrayal", 3, spans=[]), _tag("alice", "deception", 1, spans=[])], syntheses=[]
        ),
        _member(index=1, tags=[_tag("alice", "deception", 1, spans=[])], syntheses=[]),
    ]

    agreement = metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="turn")

    assert [(row["player"], row["behavior"], row["turn"], row["count"]) for row in agreement] == [
        ("alice", "deception", 1, 2),  # turn 1 first, flagged by both members
        ("bob", "betrayal", 3, 1),
    ]
    assert agreement[0]["members"] == [0, 1]


def test_subfamily_grain_merges_synonym_codes_into_one_instance():
    # the two members saw the same strong play but picked different synonym codes; at behavior
    # grain they disagree, at subfamily grain they are one instance both members flagged
    members = [
        _member(index=0, tags=[_tag("alice", "strong_move", 4, spans=[])], syntheses=[]),
        _member(index=1, tags=[_tag("alice", "coherent_target_selection", 4, spans=[])], syntheses=[]),
    ]

    at_behavior = metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="turn")
    at_subfamily = metrics.tag_agreement(members=members, code_grain="subfamily", locus_grain="turn")

    assert [row["count"] for row in at_behavior] == [1, 1]
    assert [(row["behavior"], row["count"], row["members"]) for row in at_subfamily] == [
        ("sound_strategic_play", 2, [0, 1]),
    ]


def test_family_grain_rolls_codes_up_for_counting():
    members = [
        _member(
            index=0,
            tags=[_tag("alice", "deception", 2, spans=[]), _tag("alice", "costly_honesty", 5, spans=[])],
            syntheses=[],
        ),
    ]

    at_family = metrics.tag_agreement(members=members, code_grain="family", locus_grain="turn")

    assert [(row["behavior"], row["turn"]) for row in at_family] == [("honesty", 2), ("honesty", 5)]


def test_game_locus_grain_collapses_every_turn_into_presence():
    members = [
        _member(
            index=0,
            tags=[_tag("alice", "deception", 2, spans=[]), _tag("alice", "deception", 7, spans=[])],
            syntheses=[],
        ),
        _member(index=1, tags=[_tag("alice", "deception", 9, spans=[])], syntheses=[]),
    ]

    at_game = metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="game")

    assert [(row["player"], row["behavior"], row["turn"], row["count"]) for row in at_game] == [
        ("alice", "deception", None, 2),
    ]


def test_span_locus_grain_matches_by_cited_span_overlap_not_equality():
    # members 0 and 1 cite overlapping (not identical) spans — the same event; member 2 cites
    # disjoint spans — a different event; the tag with no resolved citation clusters with nothing
    members = [
        _member(index=0, tags=[_tag("alice", "deception", 2, spans=[10, 11])], syntheses=[]),
        _member(index=1, tags=[_tag("alice", "deception", 3, spans=[11, 12])], syntheses=[]),
        _member(index=2, tags=[_tag("alice", "deception", 2, spans=[30])], syntheses=[]),
        _member(index=3, tags=[_tag("alice", "deception", 2, spans=[])], syntheses=[]),
    ]

    at_span = metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="span")

    assert [(row["spans"], row["members"], row["count"]) for row in at_span] == [
        ([], [3], 1),
        ([10, 11, 12], [0, 1], 2),
        ([30], [2], 1),
    ]


def test_locus_rejects_an_unknown_grain():
    try:
        metrics.locus(tag=_tag("alice", "deception", 1, spans=[]), locus_grain="phase")
    except ValueError as error:
        assert "phase" in str(error)
    else:
        raise AssertionError("locus should reject an unknown locus grain")
