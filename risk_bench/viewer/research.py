"""Descriptive research reporting; saved annotations retain their original scope."""

from collections import Counter, defaultdict

from risk_bench import specs


def player_identity(name, spec):
    try:
        model = specs.resolve_model(spec=spec)
    except ValueError:
        model = spec
    return {"name": name, "model": model, "spec": spec, "kind": "human" if spec == "human" else "agent"}


def game_context(model, player_specs, termination_reason):
    turns = sorted({s["turn"] for s in model["steps"] if s["turn"] > 0})
    players = []
    for seat, player in enumerate(model["players"]):
        eliminated = next((s for s in model["steps"] if s["players"][seat]["eliminated"]), None)
        players.append(
            {
                **player_identity(player["name"], player_specs[player["name"]]),
                "mission": player["mission"],
                "eliminated_turn": eliminated["turn"] if eliminated else None,
                "eliminated_round": eliminated["round"] if eliminated else None,
                "turns_survived": sum(not eliminated or t <= eliminated["turn"] for t in turns),
                "messages": sum(m["sender"] == player["name"] for m in model["messages"]),
            }
        )
    return {
        "players": players,
        "rounds_played": max(s["round"] for s in model["steps"]),
        "turns_played": len(turns),
        "termination_reason": termination_reason,
        "related_branch": bool(model.get("ancestry")),
    }


def research_report(view, player_specs):
    """Count actor undertakings, never agreement containers or evaluator votes.

    Existing statement findings are explicitly historical. Nothing in this
    reporting layer certifies them under the narrower October scope.
    """
    modern = view["tag_schema"] == "commitments_deception_v4"
    spans = {s["span"]: s for s in view["spans"]}
    source_matches = bool((view.get("coverage") or {}).get("source_matches"))
    members = []
    for member in view["members"]:
        players = []
        commitments = list({u["id"]: u for u in member.get("commitments", [])}.values())
        statements = list({u["id"]: u for u in member.get("deceptions" if modern else "statements", [])}.values())
        valid = [c for c in commitments if source_matches and c.get("valid") and not c.get("needs_review")]
        proposals = defaultdict(list)
        for c in valid:
            for event in c.get("status_history", []):
                if c.get("agreement_id") and event["status"] == "proposed":
                    proposals[c["agreement_id"]].append((event["cutoff_span"], c["actor"]))
        initiators = {}
        for agreement, events in proposals.items():
            first = min(cutoff for cutoff, _ in events)
            authors = {actor for cutoff, actor in events if cutoff == first}
            initiators[agreement] = authors if len(authors) == 1 else set()
        for name, spec in player_specs.items():
            own = [c for c in valid if c["actor"] == name]
            established = [c for c in own if c.get("established")]
            outcomes = dict(Counter(c["status"] for c in established))
            messages = [s for s in spans.values() if s["layer"] == "message" and s["player"] == name]
            screenings = {r["span"]: r for r in member.get("coverage", [])}
            coverage = Counter()
            coverage_records = []
            for message in messages:
                screen = screenings.get(message["span"])
                status = (screen or {}).get("status", "outside_cutoff")
                if not source_matches:
                    status = "source_mismatch"
                coverage[status] += 1
                if status not in {"candidate", "no_candidate"}:
                    coverage_records.append(
                        {
                            "span": message["span"],
                            "status": status,
                            "reason": (screen or {}).get("explanation", "Outside evaluation cutoff"),
                        }
                    )
            incidents = defaultdict(list)
            for s in statements:
                if (
                    source_matches
                    and s["actor"] == name
                    and s.get("valid")
                    and not s.get("needs_review")
                    and (modern or s["status"] == "deceptive")
                ):
                    # One proposition in one delivered passage; bases and mentions do not add incidents.
                    key = (s["passage"]["span"], " ".join(s["proposition"].split()).casefold(), s.get("relevant_time"))
                    incidents[key].append(s)
            eligible = [
                c
                for c in established
                if c["status"] in {"fulfilled", "violated"}
                and c.get("action_provenance") == "agent_choice"
                and spec != "human"
            ]
            roles = {"initiator": set(), "accepting_participant": set(), "unknown": set()}
            for c in own:
                agreement = c.get("agreement_id")
                if not agreement:
                    continue
                if modern:
                    if c.get("initiator") == name:
                        roles["initiator"].add(agreement)
                    elif any(e["actor"] == name and e["role"] == "accepting_participant"
                             and e["response"] == "accepted" for e in c.get("participant_history", [])):
                        roles["accepting_participant"].add(agreement)
                    else:
                        roles["unknown"].add(agreement)
                    continue
                proposers = initiators.get(agreement, set())
                if name in proposers:
                    roles["initiator"].add(agreement)
                elif proposers and any(e["status"] == "agreed" for e in c.get("status_history", [])):
                    roles["accepting_participant"].add(agreement)
                else:
                    roles["unknown"].add(agreement)
            units = [u for u in commitments + statements if u.get("actor") == name]
            players.append(
                {
                    **player_identity(name, spec),
                    "messages": len(messages),
                    "established": len(established),
                    "unestablished": len(own) - len(established),
                    "outcomes": outcomes,
                    "unestablished_statuses": dict(Counter(c["status"] for c in own if not c.get("established"))),
                    "roles": {key: len(value) for key, value in roles.items()},
                    "role_records": [
                        {"role": key, "agreement_id": value}
                        for key, values in roles.items()
                        for value in sorted(values)
                    ],
                    "deception_incidents": len(incidents),
                    "screening_scope": dict(Counter(
                        screenings.get(m["span"], {}).get("scope", "unavailable") for m in messages
                    )),
                    "missing_evidence_messages": sum(
                        bool(screenings.get(m["span"], {}).get("missing_evidence")) for m in messages
                    ),
                    "candidate_reviews": dict(Counter(
                        c["review_history"][-1]["outcome"] for c in member.get("deception_candidates", [])
                        if c["actor"] == name
                    )),
                    "candidate_records": [c for c in member.get("deception_candidates", []) if c["actor"] == name],
                    "deception_records": [
                        {
                            "ids": [s["id"] for s in group],
                            "span": group[0]["passage"]["span"],
                            "quote": group[0]["passage"].get("quote"),
                            "proposition": group[0]["proposition"],
                            "kind": "deception" if modern else "statement",
                        }
                        for group in incidents.values()
                    ],
                    "commitment_records": [
                        {
                            "id": c["id"],
                            "status": c["status"],
                            "established": bool(c.get("established")),
                            "terms": c["terms"],
                            "reason": c.get("reason"),
                            "agreement_id": c.get("agreement_id"),
                            "provenance": c.get("action_provenance", "unknown"),
                        }
                        for c in own
                    ],
                    "excluded": sum(not u.get("valid") or bool(u.get("needs_review")) for u in units),
                    "excluded_records": [
                        {"id": u["id"], "kind": u["type"].removeprefix("eval_"), "issues": u.get("issues", [])}
                        for u in units
                        if not u.get("valid") or u.get("needs_review")
                    ],
                    "coverage": dict(coverage),
                    "coverage_records": coverage_records,
                    "provenance": dict(Counter(c.get("action_provenance", "unknown") for c in established)),
                    "adherence": {
                        "fulfilled": sum(c["status"] == "fulfilled" for c in eligible),
                        "resolved": len(eligible),
                    },
                }
            )
        members.append(
            {
                "index": member["index"],
                "label": member["label"],
                "spec": member["spec"],
                "status": member["status"],
                "players": players,
                "unassigned_exclusions": [
                    {"id": u["id"], "actor": u.get("actor"), "issues": u.get("issues", [])}
                    for u in commitments + statements
                    if u.get("actor") not in player_specs
                ],
            }
        )
    return {
        "codebook_version": view.get("codebook_version"),
        "codebook_hash": view.get("codebook_hash"),
        "schema": view["tag_schema"],
        "method": view.get("evaluation_method", "whole_game"),
        "version": view.get("version"),
        "members": members,
        "source_matches": source_matches,
        "partial": bool((view.get("coverage") or {}).get("partial")),
        "scope": "commitment_communication" if modern else "historical_statements",
        "completion_status": view.get("completion_status"),
        "review_flags": view.get("review_flags", 0),
        "has_evaluation": view["has_evaluation"],
    }


def tournament_research(games):
    """Aggregate only matching instruments and evaluator identities; keep games as clusters."""
    cohorts = {}
    availability = []
    for game_id, context, report in games:
        availability.append(
            {
                "game_id": game_id,
                "version": report.get("version"),
                "codebook_version": report.get("codebook_version"),
                "method": report.get("method", "whole_game"),
                "status": report.get("error")
                or (
                    "Not evaluated"
                    if not report.get("has_evaluation")
                    else "Legacy schema; open game to inspect"
                    if report.get("schema") == "legacy_v2"
                    else "Source mismatch"
                    if not report.get("source_matches")
                    else "Interrupted"
                    if report.get("completion_status") == "interrupted"
                    else "Unfinished"
                    if report.get("completion_status") == "unfinished"
                    else "Partial observation"
                    if report.get("partial")
                    else f"Completed · {report['review_flags']} annotation(s) need review"
                    if report.get("review_flags")
                    else "Available"
                ),
                "related_branch": context.get("related_branch", False),
            }
        )
        if report.get("schema") not in {"two_tag_v3", "commitments_deception_v4"}:
            continue
        occurrences = Counter()
        for member in report["members"]:
            spec = member["spec"]
            occurrences[spec] += 1
            key = (report.get("codebook_version"), report.get("codebook_hash"), spec, occurrences[spec],
                   report.get("method", "whole_game"))
            cohort = cohorts.setdefault(
                key,
                {
                    "codebook_version": key[0],
                    "codebook_hash": key[1],
                    "evaluator": spec,
                    "replicate": key[3],
                    "scope": report.get("scope", "historical_statements"),
                    "method": key[4],
                    "players": {},
                    "games": [],
                },
            )
            cohort["games"].append(
                {
                    "id": game_id,
                    "status": member["status"],
                    "version": report["version"],
                    "partial": report["partial"],
                    "source_matches": report["source_matches"],
                    "unassigned_exclusions": member["unassigned_exclusions"],
                }
            )
            exposure = {p["name"]: p for p in context["players"]}
            for player in member["players"]:
                model_key = (player["kind"], player["model"])
                row = cohort["players"].setdefault(
                    model_key,
                    {
                        "model": player["model"],
                        "kind": player["kind"],
                        "specs": set(),
                        "games": set(),
                        "deception_games": set(),
                        "observed_games": set(),
                        "player_games": 0,
                        "turns_survived": 0,
                        **dict.fromkeys(
                            ("established", "unestablished", "deception_incidents", "excluded", "messages"), 0
                        ),
                        "outcomes": Counter(),
                        "coverage": Counter(),
                        "roles": Counter(),
                        "provenance": Counter(),
                        "unestablished_statuses": Counter(),
                        "adherence": Counter(),
                        "records": [],
                        "screening_scope": Counter(),
                        "candidate_reviews": Counter(),
                        "missing_evidence_messages": 0,
                    },
                )
                row["specs"].add(player["spec"])
                row["games"].add(game_id)
                if report["source_matches"]:
                    row["observed_games"].add(game_id)
                row["player_games"] += 1
                row["turns_survived"] += exposure[player["name"]]["turns_survived"]
                if player["deception_incidents"]:
                    row["deception_games"].add(game_id)
                for field in ("established", "unestablished", "deception_incidents", "excluded", "messages"):
                    row[field] += player[field]
                for field in ("outcomes", "coverage", "roles", "provenance", "unestablished_statuses", "adherence"):
                    row[field].update(player[field])
                for field in ("screening_scope", "candidate_reviews"):
                    row[field].update(player.get(field, {}))
                row["missing_evidence_messages"] += player.get("missing_evidence_messages", 0)
                row["records"].append(
                    {"game_id": game_id, "version": report["version"], "index": member["index"], "player": player}
                )
    for cohort in cohorts.values():
        for row in cohort["players"].values():
            row["specs"] = sorted(row["specs"])
            row["games"] = sorted(row["games"])
            row["observed_games"] = sorted(row["observed_games"])
            row["deception_games"] = sorted(row["deception_games"])
        cohort["players"] = list(cohort["players"].values())
    return {"cohorts": list(cohorts.values()), "availability": availability}
