"""Pure metric core shared by the evaluation reader and the post-eval analysis dump.

These functions operate on the *member view-model shape*: the ``tags`` and ``syntheses`` dicts
(each citation already carrying ``resolved`` / ``quote`` / ``quote_matched``) that the evaluation
view model builds. Keeping the integrity and agreement maths here, rather than inside the viewer,
means the reader and the analysis artifact compute the same numbers from one implementation and can
never drift apart — the analysis simply re-verifies each citation before feeding it in.
"""

from collections.abc import Iterable
from typing import Any, Final

from risk_bench.runner import commentary

LOCUS_GRAINS: Final[tuple[str, ...]] = ("span", "turn", "game")
"""The locus grains ``locus`` accepts, finest to coarsest."""


def integrity(members: list[dict[str, Any]]) -> dict[str, int]:
    """Count citation-integrity totals across every member's evidence.

    Args:
        members: The evaluation members, each a dict with ``tags`` and ``syntheses`` in the
            view-model shape.

    Returns:
        The panel totals: ``total_citations``, ``broken_citations``, ``quoted_citations``, and
        ``quote_mismatches``.
    """
    return integrity_of_evidence(
        evidence=(citation for member in members for citation in evidence_of_member(member=member))
    )


def integrity_of_evidence(evidence: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count citation-integrity totals over a flat stream of evidence citations.

    Args:
        evidence: Evidence dicts, each carrying ``resolved``, ``quote``, and ``quote_matched``.

    Returns:
        ``total_citations``, ``broken_citations``, ``quoted_citations``, and ``quote_mismatches``
        over the stream.
    """
    total_citations = broken_citations = quoted_citations = quote_mismatches = 0
    for citation in evidence:
        total_citations += 1
        broken_citations += 0 if citation["resolved"] else 1
        if citation["quote"] is not None:
            quoted_citations += 1
            quote_mismatches += 0 if citation["quote_matched"] else 1
    return {
        "total_citations": total_citations,
        "broken_citations": broken_citations,
        "quoted_citations": quoted_citations,
        "quote_mismatches": quote_mismatches,
    }


def locus(tag: dict[str, Any], locus_grain: str) -> frozenset[int] | int | None:
    """Project a tag's location in the game to the chosen locus grain.

    Args:
        tag: One tag in the view-model shape (a ``turn`` plus resolved ``evidence`` citations).
        locus_grain: One of LOCUS_GRAINS — "span" (the set of decision-loci the tag's resolved
            evidence cites), "turn" (the tag's turn), or "game" (no location: presence anywhere).

    Returns:
        The locus key: a frozenset of cited span ids, the turn number, or None for "game".

    Raises:
        ValueError: If the grain is not a known locus grain.
    """
    if locus_grain == "span":
        return frozenset(citation["span"] for citation in tag["evidence"] if citation["resolved"])
    if locus_grain == "turn":
        return tag["turn"]
    if locus_grain == "game":
        return None
    raise ValueError(f"unknown locus grain {locus_grain!r}; expected one of {', '.join(LOCUS_GRAINS)}")


def tag_agreement(members: list[dict[str, Any]], code_grain: str, locus_grain: str) -> list[dict[str, Any]]:
    """Group every tagged instance at the chosen grain by which members flagged it.

    A raw tag reduces to the grouping key ``(player, project(behavior, code_grain), locus)``;
    identical keys across members are one instance, so the rows serve both as the deduplicated
    instance inventory and as the cross-member agreement signal. At "span" locus grain a tag's
    locus is a *set* of cited spans, so instances form by set overlap (tags whose cited spans
    intersect are the same event) rather than key equality.

    Caution: matching at "family" or "axis" code grain over-credits agreement, because several
    distinct behaviors stack into one cell; use those grains for frequency and inventory only and
    match agreement at "behavior" or "subfamily" (both ~1:1 per rater and cell).

    Args:
        members: The evaluation members, each a dict with an ``index`` and ``tags``.
        code_grain: How far up the behaviour taxonomy to roll each tag (see commentary.CODE_GRAINS).
        locus_grain: How coarsely to place each tag in the game (see LOCUS_GRAINS).

    Returns:
        One row per instance carrying ``player``, ``behavior`` (the projected grouping key), the
        locus (``turn`` for the turn/game grains, None at game grain; sorted ``spans`` at span
        grain), the sorted member indices that flagged it, and their ``count``. Turn/game rows
        are ordered by locus then player; span rows by earliest cited span then player.
    """
    if locus_grain == "span":
        return _agreement_by_span_overlap(members=members, code_grain=code_grain)
    return _agreement_by_key_equality(members=members, code_grain=code_grain, locus_grain=locus_grain)


def evidence_of_member(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect every citation a member made — tag evidence then synthesis evidence — in reading order.

    Args:
        member: One evaluation member in the view-model shape.

    Returns:
        The flattened evidence dicts across the member's tags, synthesis profile, and key episodes.
    """
    return [
        *tag_evidence(member=member),
        *synthesis_profile_evidence(member=member),
        *key_episode_evidence(member=member),
    ]


def tag_evidence(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the citations attached to a member's per-turn tags.

    Args:
        member: One evaluation member in the view-model shape.

    Returns:
        The evidence dicts cited by the member's tags, in tag order.
    """
    return [citation for tag in member["tags"] for citation in tag["evidence"]]


def synthesis_profile_evidence(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the citations attached to a member's per-dimension synthesis characterizations.

    Args:
        member: One evaluation member in the view-model shape.

    Returns:
        The evidence dicts cited across the member's synthesis profile dimensions.
    """
    return [
        citation
        for synthesis in member["syntheses"]
        for dimension in synthesis["profile"]
        for citation in dimension["evidence"]
    ]


def key_episode_evidence(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the citations a member attached to its per-player key episodes.

    Args:
        member: One evaluation member in the view-model shape.

    Returns:
        The evidence dicts cited by the member's key episodes, across its syntheses.
    """
    return [citation for synthesis in member["syntheses"] for citation in synthesis["key_episodes"]]


def _agreement_by_key_equality(
    members: list[dict[str, Any]], code_grain: str, locus_grain: str
) -> list[dict[str, Any]]:
    # the locus is the turn number at turn grain and None at game grain (span grain never reaches
    # this path — it matches by overlap, not equality), so keys within one call always compare
    flaggers: dict[tuple[str, str, frozenset[int] | int | None], set[int]] = {}
    for member in members:
        for tag in member["tags"]:
            key = (
                tag["player"],
                commentary.project(behavior=tag["behavior"], level=code_grain),
                locus(tag=tag, locus_grain=locus_grain),
            )
            flaggers.setdefault(key, set()).add(member["index"])
    return [
        {
            "player": player,
            "behavior": behavior,
            "turn": turn,
            "members": sorted(indices),
            "count": len(indices),
        }
        for (player, behavior, turn), indices in sorted(flaggers.items(), key=lambda item: (item[0][2], item[0][0]))
    ]


def _agreement_by_span_overlap(members: list[dict[str, Any]], code_grain: str) -> list[dict[str, Any]]:
    # tags citing intersecting span sets are reads of the same event: within each (player, code)
    # group, cluster tags into connected components over locus overlap, so a member joins an
    # instance by citing any of its spans rather than the identical set — the detection metric
    # from the reliability analysis. A tag with no resolved citation clusters with nothing.
    tags_by_group: dict[tuple[str, str], list[tuple[frozenset[int], int]]] = {}
    for member in members:
        for tag in member["tags"]:
            group = (tag["player"], commentary.project(behavior=tag["behavior"], level=code_grain))
            loci = frozenset(citation["span"] for citation in tag["evidence"] if citation["resolved"])
            tags_by_group.setdefault(group, []).append((loci, member["index"]))
    rows = [
        {
            "player": player,
            "behavior": code,
            "spans": sorted(cluster_spans),
            "members": sorted(cluster_members),
            "count": len(cluster_members),
        }
        for (player, code), tagged in tags_by_group.items()
        for cluster_spans, cluster_members in _overlap_clusters(tagged=tagged)
    ]
    rows.sort(key=lambda row: (row["spans"][0] if row["spans"] else -1, row["player"], row["behavior"]))
    return rows


def _overlap_clusters(tagged: list[tuple[frozenset[int], int]]) -> list[tuple[set[int], set[int]]]:
    clusters: list[tuple[set[int], set[int]]] = []
    for loci, member_index in tagged:
        merged_spans, merged_members = set(loci), {member_index}
        untouched = []
        for spans, member_indices in clusters:
            if loci and spans & loci:
                merged_spans |= spans
                merged_members |= member_indices
            else:
                untouched.append((spans, member_indices))
        clusters = [*untouched, (merged_spans, merged_members)]
    return clusters
