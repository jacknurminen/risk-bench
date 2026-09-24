"""Descriptive counts and transparent source-anchor comparisons for commitments."""

from collections import Counter, defaultdict

from risk_bench.runner.codebook import STATUSES, citations


def integrity_of_evidence(evidence):
    rows = list(evidence)
    return {
        "total_citations": len(rows),
        "broken_citations": sum(not e["resolved"] for e in rows),
        "quoted_citations": sum(bool(e.get("quote")) for e in rows),
        "quote_mismatches": sum(e.get("quote_matched") is False for e in rows),
    }


def member_summary(member):
    episodes = [e for e in member["episodes"] if e["valid"]]
    claims = [c for c in member["claims"] if c["valid"]]
    eligible = [
        e for e in episodes if e["status"] in {"fulfilled", "violated"} and e["action_provenance"] == "agent_choice"
    ]
    fulfilled = sum(e["status"] == "fulfilled" for e in eligible)
    coverage = Counter(r["status"] for r in member["coverage"])
    return {
        "index": member["index"],
        "label": member["label"],
        "status": member["status"],
        "episode_count": len(episodes),
        "claim_count": len(claims),
        "statuses": {s: sum(e["status"] == s for e in episodes) for s in STATUSES},
        "provenance": dict(Counter(e["action_provenance"] for e in episodes)),
        "adherence": {
            "fulfilled": fulfilled,
            "resolved_agent_episodes": len(eligible),
            "proportion": fulfilled / len(eligible) if eligible else None,
        },
        "claim_statuses": dict(Counter(c["status"] for c in claims)),
        "claims_by_basis": {
            basis: {
                "population": sum(c["basis"] == basis for c in claims),
                "codes": dict(Counter(code for c in claims if c["basis"] == basis for code in c["codes"])),
            }
            for basis in ("stated_plan", "recorded_fact", None)
        },
        "coverage": dict(coverage),
        "message_count": len(member["coverage"]),
        "invalid_units": sum(not u["valid"] for key in ("agreements", "episodes", "claims") for u in member[key]),
        "integrity": integrity_of_evidence(citations(member)),
    }


def comparisons(members):
    # IDs are evaluator-local. Match only explicit source anchors, exposing split/merge disagreements.
    result = {}
    for plural, anchor in (("episodes", "undertaking"), ("claims", "message"), ("coverage", None)):
        groups = defaultdict(dict)
        for member in members:
            for unit in member[plural]:
                if anchor:
                    cite = unit[anchor]
                    key = (unit["actor"], cite["span"], cite.get("quote") or "", unit.get("basis"))
                else:
                    key = (None, unit["span"], "", None)
                groups[key].setdefault(member["index"], []).append(
                    {
                        "id": unit.get("id"),
                        "status": unit["status"],
                        "codes": unit.get("codes", []),
                        "valid": unit.get("valid", True),
                    }
                )
        result[plural] = [
            {
                "actor": key[0],
                "span": key[1],
                "quote": key[2],
                "basis": key[3],
                "members": votes,
                "ambiguous_alignment": any(len(v) > 1 for v in votes.values()),
            }
            for key, votes in sorted(groups.items(), key=lambda item: item[0][1])
        ]
    return result
