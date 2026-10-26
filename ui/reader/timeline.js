/* ================= LEFT RAIL ================= */
function sidebarPlayerName(name) {
  return !/#\d+$/.test(name) && GAME.players.some(player => player.name.startsWith(name + '#'))
    ? name + '#1' : name;
}
function playerResult(name) {
  const last = steps[steps.length - 1];
  const seat = seatOf[name];
  if (GAME.result && GAME.result.winner === name) return ["winner", "var(--coop)"];
  if (last.players[seat].eliminated) return ["eliminated", "var(--adversarial)"];
  return [GAME.result ? "survived" : "in game", "var(--gold)"];
}
function renderRail() {
  el("railPlayers").innerHTML = GAME.players.map((p, seat) => {
    const [rl, rc] = playerResult(p.name);
    const hasNotes = GAME.notes.some(n => n.player === p.name);
    return `<article class="card pcard" id="pcard-${seat}" style="border-top-color:${COLORS[seat]}">
      <div class="top">
        <span class="badge" style="background:${COLORS[seat]}">${initialsOf(p.name)}</span>
        <div style="flex:1;min-width:0">
          <div class="playerHeading">
            <span class="ptag" style="color:${COLORS[seat]}">${escapeHtml(sidebarPlayerName(p.name))}</span>
            ${hasNotes ? `<button class="iconbtn" onclick="openNotes('${p.name}')" title="all private notes" style="margin-left:auto;font-size:12px">📝</button>` : ""}
          </div>
          <div class="playerLabels">
            <span class="presult" style="color:${rc};background:color-mix(in srgb, ${rc} 13%, #fff)">${rl}</span>
            ${p.experiment_profile ? `<span class="experimentBadge" title="Experimental player · visible only in the UI">${escapeHtml(p.experiment_profile.label)}</span>` : ""}
          </div>
          <div class="pstats" id="prstats-${seat}"></div>
        </div>
      </div>
      <div class="pmission">${humanPlayers.size && hideOpponentMissions && !humanPlayers.has(p.name) ? "Mission hidden" : `▸ ${escapeHtml(p.mission)}`}</div>
      ${humanPlayers.has(p.name) ? `<section class="playerHand" aria-label="Your cards"><div class="handHead"><strong>Your cards</strong><span id="handContext-${seat}"></span></div><div id="playerHand-${seat}"></div></section>` : ""}
    </article>`;
  }).join("");
  renderFilters();
  renderMissionToggle();
  renderPlayerHands(steps[index]);
}
function renderMissionToggle() {
  const toggle = el("missionToggle");
  toggle.hidden = !humanPlayers.size;
  toggle.classList.toggle("on", hideOpponentMissions);
  toggle.setAttribute("aria-checked", String(hideOpponentMissions));
  toggle.querySelector(".switch").classList.toggle("on", hideOpponentMissions);
}
el("missionToggle").onclick = () => {
  hideOpponentMissions = !hideOpponentMissions;
  try { localStorage.setItem("risk-bench:hide-opponent-missions", String(hideOpponentMissions)); } catch {}
  renderRail(); show(index);
};

function renderPlayerHands(step) {
  GAME.players.forEach((player, seat) => {
    const container = el("playerHand-" + seat);
    if (!container) return;
    const cards = step.hands?.[player.name];
    const historical = index !== steps.length - 1;
    el("handContext-" + seat).textContent = historical ? `${gameTimeLabel(step.turn, step.round)} · step ${index}` : "Latest board";
    container.innerHTML = cards?.length ? cards.map(card =>
      `<div class="heldCard"><span class="cardDesign">${escapeHtml(placeName(card.design))}</span><span>${card.territory ? `<span class="tlink" data-place="${card.territory}">${escapeHtml(placeName(card.territory))}</span>` : "Wild card"}</span></div>`).join("")
      : `<p class="handEmpty">${cards ? "No cards held." : "Card details unavailable for this snapshot."}</p>`;
  });
}

function renderFilters() {
  const select = (id, values, current, label) => {
    el(id).innerHTML = '<option value="">All ' + label + '</option>' +
      values.map(v => '<option value="' + escapeHtml(v) + '"' + (v === current ? " selected" : "") + '>' + escapeHtml(pretty(v)) + '</option>').join("");
  };
  select("actorFilter", GAME.players.map(p => p.name), state.filters.actor, "players");
  select("codeFilter", EVAL.codes || [], state.filters.code, "codes");
  select("statusFilter", isThesisSchema() ? ["proposed","agreed","fulfilled","violated","unresolved","closed"] : isTwoTagSchema() ?
    ["proposed","agreed","committed","fulfilled","violated","unresolved","closed","contradicted","deceptive",
      ...["made","supported","undetermined"].filter(status => allUnits().some(u => u.status === status))] :
    ["open","fulfilled","violated","released","not_triggered","unassessable","assessable","unclear","insufficient_evidence"], state.filters.status, "statuses");
  select("basisFilter", ["stated_plan","recorded_fact","unknown"], state.filters.basis, "evidence bases");
  el("annList").innerHTML = annotators().map(a => '<button class="annrow ' + (state.filters.annotators.has(a.index) ? "on" : "off") + '" onclick="toggleAnnotator(' + a.index + ')">' +
    '<span class="swatch" style="background:' + annotatorColor(a.index) + '"></span>' +
    '<span class="annname">' + escapeHtml(a.label) + '</span><span class="checkbox ' + (state.filters.annotators.has(a.index) ? "on" : "") + '"></span></button>').join("");
  for (const [id, key] of [["privToggle","showPrivate"],["commToggle","showCommentary"]]) {
    el(id).className = "toggle" + (state.filters[key] ? " on" : "");
    el(id).querySelector(".switch").className = "switch" + (state.filters[key] ? " on" : "");
  }
}
for (const key of ["actor","code","status","basis"]) el(key + "Filter").onchange = e => {
  state.filters[key] = e.target.value; renderTimeline();
};
function toggleAnnotator(idx) {
  const s = state.filters.annotators; s.has(idx) ? s.delete(idx) : s.add(idx);
  renderFilters(); renderTimeline();
}
el("privToggle").onclick = () => { state.filters.showPrivate = !state.filters.showPrivate; renderFilters(); renderTimeline(); };
el("commToggle").onclick = () => {
  researchVisibilityChosen = true;
  state.filters.showCommentary = !state.filters.showCommentary;
  renderFilters(); renderEvalBar(); renderSynthesis(); renderTimeline();
};
const KINDS = {
  thought: ["decision summary", "var(--deceptive)"], message: ["delivered message", "var(--blue)"],
  action: ["applied action", "#8a7b57"], event: ["engine event", "#9aa3ae"], note: ["private note", "#b07d2e"],
};
function renderTimeline() {
  const root = el("timeline");
  const compactTags = state.mode === "research" && isTwoTagSchema();
  el("expandAll").hidden = compactTags;
  el("collapseAll").hidden = compactTags;
  if (state.mode === "research") root.innerHTML = researchHtml();
  else if (state.mode === "compare") root.innerHTML = comparisonHtml();
  else root.innerHTML = turns.map(turn => turnCard(turn) + reviewTimelineEvents(turn) +
    activeReviewTimelineEvent(turn) + pausedReviewTimelineEvent(turn)).join("");
  el("turnCount").textContent = turns.length + " turns";
  el("subStat").textContent = EVAL.has_evaluation
    ? (isTwoTagSchema() ? orderedV3Units() : allUnits()).filter(u => ["episode","commitment"].includes(u.kind)).length + " commitments · " +
      allUnits().filter(u => ["claim","statement","deception"].includes(u.kind)).length +
      (isThesisSchema() ? " documented deception incidents" : isTwoTagSchema() ? " statement assessments" : " claim assessments")
    : "No evaluation yet";
  show(index);
}
function turnCard(turn) {
  const setup = isSetupTurn(turn), actor = turnActor(turn), accent = setup ? "var(--mute)" : colorOf(actor);
  const units = state.filters.showCommentary ?
    (isTwoTagSchema() ? orderedV3Units() : allUnits()).filter(u => unitVisible(u) &&
      (u.turn === turn || (u.members || [u]).some(m =>
        m.turn === turn || (m.mentions || []).some(c => spanById.get(c.span)?.turn === turn)))) : [];
  return '<article class="tcard" data-turn="' + turn + '" style="border-left-color:' + accent + '">' +
    '<button class="thead" onclick="toggleExpand(' + turn + ')"><span class="tbadge" style="background:' + accent + '">' +
    (setup ? "⚑" : initialsOf(actor)) + '</span><span class="tmeta"><span class="line1">' +
    '<span class="ttag">' + (setup ? "Setup placement" : escapeHtml(tagOf(actor))) + '</span><span class="tturn">' +
    gameTimeLabel(turn, roundByTurn[turn]) + '</span></span><span class="tsummary">' + richText(turnSummary(turn)) +
    '</span><span class="turnHighlights">' + turnHighlights(turn).map(h => '<span class="moveChip">' + h.label + '</span>').join("") +
    '</span></span><span class="chev">⌄</span></button>' +
    (units.length ? '<div class="tpills">' + units.map(unitPill).join("") + '</div>' : "") +
    (state.expanded.has(turn) ? expandedBody(turn) : "") + '</article>';
}
function reviewTimelineEvents(turn) {
  if (!state.filters.showCommentary || !EVAL.coverage?.source_matches) return "";
  const roundNotes = (EVAL.live_commentary || []).filter(note =>
    note.turn === turn && state.filters.annotators.has(note.evaluator_index));
  const finalNotes = (EVAL.live_final || []).filter(note =>
    note.turn === turn && state.filters.annotators.has(note.evaluator_index));
  const event = (note, final) => '<article class="researchReviewEvent" data-turn="' + turn + '">' +
    '<div class="researchReviewMeta">' + (final ? (EVAL.evaluation_method === 'whole_game'
      ? 'Whole-game analysis' : 'Final research review') :
      'Research review · Round ' + escapeHtml(String(note.round ?? roundByTurn[turn] ?? '?'))) +
    ' · ' + escapeHtml(annotatorLabel(note.evaluator_index)) + '</div>' +
    '<div class="researchReviewText">' + richText(note.text) + '</div></article>';
  return roundNotes.map(note => event(note, false)).join('') +
    finalNotes.map(note => event(note, true)).join('');
}
function activeReviewTimelineEvent(turn) {
  if (!state.filters.showCommentary || !EVAL.review_active) return "";
  if (EVAL.reviewing_whole_game) {
    if (turn !== turns.at(-1)) return "";
    return '<article class="researchReviewEvent activeReviewEvent" role="status"><div class="researchReviewMeta">◐ Whole-game analysis in progress</div></article>';
  }
  if (EVAL.reviewing_final) {
    if (turn !== turns.at(-1)) return "";
    return '<article class="researchReviewEvent activeReviewEvent" role="status"><div class="researchReviewMeta">◐ Final research review in progress</div></article>';
  }
  const round = EVAL.reviewing_round;
  const roundOf = value => roundByTurn[value] ?? (spansByTurn.get(value) || []).find(span => span.round != null)?.round;
  if (roundOf(turn) !== round || turns.some(later => later > turn && roundOf(later) === round)) return "";
  return '<article class="researchReviewEvent activeReviewEvent" role="status"><div class="researchReviewMeta">◐ Research review · Round ' +
    escapeHtml(String(round)) + ' in progress</div></article>';
}
function pausedReviewTimelineEvent(turn) {
  if (!state.filters.showCommentary || !GAME.result || !EVAL.live_evaluation || EVAL.live_finalized ||
      EVAL.running || !EVAL.error) return "";
  const roundOf = value => roundByTurn[value] ?? (spansByTurn.get(value) || []).find(span => span.round != null)?.round;
  const pendingTurn = turns.find(value => value > 0 && roundOf(value) > (EVAL.evaluated_through_round || 0));
  const pendingRound = pendingTurn == null ? null : roundOf(pendingTurn);
  const target = pendingRound == null ? turns.at(-1) : turns.filter(value => roundOf(value) === pendingRound).at(-1);
  if (turn !== target) return "";
  const title = pendingRound == null ? 'Final research review paused' :
    'Research review · Round ' + escapeHtml(String(pendingRound)) + ' paused';
  return '<article class="researchReviewEvent pausedReviewEvent"><div class="researchReviewMeta">' + title + '</div>' +
    '<button class="btn" onclick="resumeLiveReview()">Resume review</button></article>';
}
function expandedBody(turn) {
  const lines = (spansByTurn.get(turn) || []).filter(s =>
    state.filters.showPrivate || !["thought","note"].includes(s.layer));
  const rows = []; let cursor = null;
  for (const s of lines) {
    if (s.cursor && s.cursor.before !== cursor?.before) rows.push(forkSeparator(s.cursor.before));
    cursor = s.cursor || cursor;
    // State records remain citable in the raw trace; retain their fork boundaries here.
    if (s.layer !== "state") rows.push(traceLine(turn, s));
  }
  if (cursor && turn === turns.at(-1)) rows.push(forkSeparator(cursor.after));
  return '<div class="expand">' + rows.join("") + '</div>';
}
function traceLine(turn, s) {
  const label = s.layer === "message" ? (s.visibility === "private" ? "private message · to " + s.recipients[0] : "public message") : (KINDS[s.layer]?.[0] || s.layer);
  const content = '<div class="traceline" onclick="lineClick(event,' + s.step + ')"><button class="chip" title="Open in raw trace" onclick="openLineDrawer(' + s.span + ')">[' + s.span + ']</button>' +
    '<span><span class="kindlabel">' + escapeHtml(label) + (s.inherited ? " · inherited" : "") +
    (s.origin ? " · " + escapeHtml(pretty(s.origin)) : "") + '</span>' +
    '<span class="tracetext ' + (s.layer === "message" ? "msg" : "mono") + '">' + richText(s.text) + '</span></span></div>';
  return ["thought","note"].includes(s.layer) ? '<details class="privateTrace"><summary>' + escapeHtml(label) +
    ' · ' + escapeHtml(s.player || "") + '</summary>' + content + '</details>' : content;
}
function unitPill(u) {
  if (isTwoTagSchema()) {
    return '<button class="pill turnTag tagKind-' + u.kind +
      '" title="' + escapeHtml(unitTitle(u)) + '" onclick="openUnitDrawer(\'' + unitKey(u) + '\')">' +
      '<span class="turnTagNumber">' + v3Number(u) + '</span>' +
      escapeHtml(unitType(u) + ' · ') + unitPlayersV3(u) +
      ' · ' + unitStatusesV3(u) + escapeHtml(' · “' + (u.passage?.quote || '').slice(0, 55) + '”') +
      (u.needs_review || !u.valid ? ' · needs review' : '') + '</button>';
  }
  return '<button class="pill researchTone-' + unitTone(u) + '" title="' + escapeHtml(unitTitle(u)) + '" onclick="openUnitDrawer(\'' + unitKey(u) + '\')">' +
    escapeHtml(unitType(u) + " · " + tagOf(u.actor || u.initiator) + " · " + unitStatus(u)) +
    (u.valid ? "" : " · needs review") + '</button>';
}
function unitType(u) { return {agreement:"Proposal / agreement", episode:"Commitment", claim:"Claim",statement:"Statement",commitment:"Commitment",deception:"Deception"}[u.kind]; }
function unitTitle(u) {
  if (["statement","deception"].includes(u.kind)) return u.proposition;
  if (u.kind === "commitment") return typeof u.terms === "string" ? u.terms : u.terms?.action || u.passage?.quote || '';
  return u.kind === "episode" ? u.terms.action : u.kind === "claim" ? u.proposition :
    (u.proposals.at(-1)?.terms || "Proposal terms not recorded");
}
function unitStatus(u) {
  if (["statement","commitment"].includes(u.kind)) return pretty(u.status);
  if (u.kind === "claim") {
    if (u.codes?.includes("message_contradicted")) return "Contradicted";
    if (u.codes?.includes("message_supported")) return "Supported";
  }
  if (u.kind !== "agreement") return pretty(u.status);
  const responses = u.proposals.at(-1)?.responses || [];
  const statuses = [...new Set(responses.map(r => r.status))];
  return statuses.length === 1 ? ({accepted:"Accepted", rejected:"Rejected", no_response:"No response",
    counterproposed:"Counterproposal", unclear:"Unclear response"}[statuses[0]]) :
    (statuses.length ? "Mixed responses" : "Proposal");
}
function unitTone(u) {
  if (!u.valid) return "neutral";
  if (u.kind === "deception") return "deceptive";
  if (u.status === "deceptive") return "deceptive";
  if (u.status === "contradicted") return "adversarial";
  if (u.status === "supported") return "coop";
  if (["proposed","agreed","committed","made","unresolved"].includes(u.status)) return "open";
  if (u.codes?.includes("deceptive_communication")) return "deceptive";
  if (u.status === "violated" || u.codes?.includes("message_contradicted")) return "adversarial";
  if (u.status === "fulfilled" || u.codes?.includes("message_supported")) return "coop";
  if (u.status === "open") return "open";
  return "neutral";
}
function linkedUnitButton(u, kind, id, label) {
  const linked = allUnits().find(v => v.annotator === u.annotator && v.kind === kind && v.id === id);
  return '<button class="btn researchLink" onclick="openUnitDrawer(\'' + u.annotator + ':' + kind + ':' + id + '\')">' +
    escapeHtml(label + (linked ? ': ' + unitTitle(linked) : '')) + '</button>';
}
function researchHtml() {
  if (isTwoTagSchema()) return researchHtmlV3();
  if (!state.filters.showCommentary) return '<p class="empty">Enable annotations to see the research assessments.</p>';
  const units = allUnits().filter(unitVisible);
  const kindOptions = [["","All records"],["episode","Commitments"],["agreement","Proposals"],["claim","Claims"]];
  const active = [state.filters.validOnly ? "valid records" : "", state.filters.status && pretty(state.filters.status),
    state.filters.code && pretty(state.filters.code), state.filters.basis && pretty(state.filters.basis)].filter(Boolean);
  const toolbar = '<div class="researchToolbar"><div class="researchKinds">' + kindOptions.map(([kind,label]) =>
    '<button class="' + (state.filters.kind === kind ? 'on' : '') + '" onclick="setResearchKind(\'' + kind + '\')">' + label + '</button>').join('') +
    '</div>' + (active.length ? '<span class="researchActive">Showing ' + escapeHtml(active.join(' · ')) +
    ' <button onclick="clearResearchFilters()">Clear filters</button></span>' : '') + '</div>';
  const screening = '<details class="card researchCard"><summary>Message screening · candidates, ordinary messages and missing work</summary>' +
    (EVAL.members || []).filter(m => state.filters.annotators.has(m.index)).map(m =>
      '<h3>' + escapeHtml(m.label) + '</h3>' + m.coverage.filter(c => !state.filters.actor ||
        spanById.get(c.span)?.player === state.filters.actor).map(c =>
        '<p><button class="chip" onclick="openLineDrawer(' + c.span + ')">[' + c.span + ']</button> <strong>' +
        escapeHtml(pretty(c.status)) + '</strong> · ' + escapeHtml(c.explanation) + '</p>').join("")).join("") + '</details>';
  const headings = {episode:"Commitments", agreement:"Proposals & agreements", claim:"Claims"};
  return toolbar + '<p class="researchGuide">Commitments track one player’s promise over time. Proposals group offers and replies, including rejected offers. Claims assess what a message says. Expand a row for terms and evidence.</p>' +
    '<div class="researchLegend"><span class="researchTone-coop">Fulfilled / supported</span><span class="researchTone-adversarial">Violated / contradicted</span>' +
    '<span class="researchTone-deceptive">Deceptive communication</span><span class="researchTone-open">Open</span><span class="researchTone-neutral">Other / needs review</span></div>' +
    (units.length ? ["episode","agreement","claim"].filter(kind => units.some(u => u.kind === kind)).map(kind => '<h3 class="researchGroup">' +
      headings[kind] + ' <span>' + units.filter(u => u.kind === kind).length + '</span></h3>' +
      units.filter(u => u.kind === kind).map(u => unitCard(u)).join("")).join("") : '<p class="empty">No research units match these filters.</p>') +
    (state.filters.kind || active.length ? '' : screening);
}
function setResearchKind(kind) { state.filters.kind = kind; renderTimeline(); }
function researchHtmlV3() {
  if (!state.filters.showCommentary) return '<p class="empty">Enable annotations to see the research assessments.</p>';
  const units = orderedV3Units().map((u,index) => ({u,number:index+1})).filter(({u}) => unitVisible(u));
  const choices = [["","All tags"],isThesisSchema() ? ["deception","Documented deception"] : ["statement","Statements"],["commitment","Commitments"]];
  const toolbar = '<div class="researchToolbar"><div class="researchKinds">' + choices.map(([kind,label]) =>
    '<button class="' + (state.filters.kind === kind ? 'on' : '') + '" onclick="setResearchKind(\'' + kind + '\')">' + label + '</button>').join('') +
    '</div><button class="btn" onclick="clearResearchFilters()">Clear filters</button></div>';
  const screening = '<details class="card researchCard"><summary>Message screening</summary>' +
    (EVAL.members || []).filter(m => state.filters.annotators.has(m.index)).map(m =>
      '<h3>' + escapeHtml(m.label) + '</h3>' + (m.coverage || []).map(c =>
        '<p><button class="chip" onclick="openLineDrawer(' + c.span + ')">[' + c.span + ']</button> ' +
        escapeHtml((c.scope ? pretty(c.scope) + ' · ' : '') + pretty(c.status) + ' · ' + c.explanation) +
        (c.missing_evidence?.length ? ' · Missing evidence: ' + escapeHtml(c.missing_evidence.join('; ')) : '') + '</p>').join('')).join('') + '</details>';
  return toolbar + '<div class="tagTimeline" aria-label="Research tags in chronological order">' +
    units.map(({u,number}) => unitCardV3(u,false,number)).join('') + '</div>' +
    (units.length ? '' : '<p class="empty">No tags match these filters.</p>') + candidateReviewHtml() + screening;
}
function candidateReviewHtml() {
  if (!isThesisSchema()) return '';
  return '<details class="card researchCard"><summary>Deception candidate review — not findings</summary>' +
    (EVAL.members || []).filter(m => state.filters.annotators.has(m.index)).map(m =>
      '<h3>' + escapeHtml(m.label) + '</h3>' + ((m.deception_candidates || []).map(c =>
        '<article><h4>' + escapeHtml(c.actor + ': ' + c.proposition) + '</h4><button class="chip" onclick="openLineDrawer(' +
        c.passage.span + ')">Message [' + c.passage.span + ']</button><p>' + escapeHtml(c.scope_explanation) + '</p>' +
        c.review_history.map(r => '<p><strong>' + escapeHtml(pretty(r.outcome)) + '</strong> · ' +
          escapeHtml(v3Time(r.cutoff_span) + ' · ' + r.reason) + ' ' +
          (r.evidence || []).map(e => '<button class="chip" onclick="openLineDrawer(' + e.span + ')">[' + e.span + ']</button>').join('') +
          '</p>').join('') + (c.needs_review ? '<p>Technical review: ' + escapeHtml(c.issues.join('; ')) + '</p>' : '') +
        '</article>').join('') || '<p>No candidates recorded.</p>')).join('') + '</details>';
}
function orderedV3Units() {
  const passageOffset = u => {
    const text = spanById.get(u.passage?.span)?.text;
    const offset = text && u.passage?.quote ? text.indexOf(u.passage.quote) : -1;
    return offset < 0 ? Infinity : offset;
  };
  const groups = new Map(), units = [];
  for (const u of allUnits()) {
    if (u.kind !== 'commitment' || !u.agreement_id) { units.push(u); continue; }
    const key = u.annotator + ':' + u.agreement_id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(u);
  }
  for (const [key,members] of groups) {
    members.sort((a,b) => a.passage.span - b.passage.span || passageOffset(a) - passageOffset(b) || a.id.localeCompare(b.id));
    const lead = members[0];
    units.push({...lead, id:lead.agreement_id, members,
      actor:lead.initiator || lead.actor, status:members.length > 1 ? 'mixed' : lead.status,
      established:members.some(m => m.established),
      valid:members.some(m => m.valid), needs_review:members.some(m => m.needs_review),
      verified:members.every(m => m.verified)});
  }
  return units.filter(u => ['statement','commitment','deception'].includes(u.kind)).sort((a,b) =>
    (a.passage?.span ?? Infinity) - (b.passage?.span ?? Infinity) ||
    passageOffset(a) - passageOffset(b) || a.annotator - b.annotator || a.kind.localeCompare(b.kind) ||
    a.actor.localeCompare(b.actor) || a.id.localeCompare(b.id));
}
function v3Number(u) {
  return orderedV3Units().findIndex(v => unitKey(v) === unitKey(u) ||
    v.members?.some(m => unitKey(m) === unitKey(u))) + 1;
}
function v3Time(span) {
  const source = spanById.get(span);
  return source ? gameTimeLabel(source.turn, source.round ?? roundByTurn[source.turn]) : 'Time unknown';
}
function unitCardV3(u, expanded = false, number = v3Number(u)) {
  const source = spanById.get(u.passage?.span);
  const excerpt = u.passage?.quote || '';
  return '<article class="tagEvent tagKind-' + u.kind + ' researchTone-' + unitTone(u) + (expanded ? ' selected' : '') +
    '"' + (expanded ? ' id="tag-event-' + number + '"' : '') +
    '><button class="tagEventHead" aria-expanded="' + expanded + '" onclick="openUnitDrawer(\'' +
    unitKey(u) + '\')"><span class="tagNumber">' + number + '</span><span class="tagEventMain">' +
    '<span class="tagEventMeta">' + escapeHtml(source ? v3Time(source.span) : 'Time unknown') + ' · ' +
    unitPlayersV3(u) +
    '</span><span class="tagEventTitle">“' + escapeHtml(excerpt) + '”</span></span>' +
    '<span class="tagEventLabels"><span class="tagType">' + escapeHtml(unitType(u)) + '</span>' +
    unitStatusesV3(u) +
    (u.kind === 'commitment' && !u.established && u.status !== 'proposed' ?
      '<span class="tagUnestablished">Never established</span>' : '') +
    (u.needs_review ? '<span class="tagReview">Needs review</span>' : '') +
    '</span></button>' + (expanded ? unitDetailV3(u) : '') + '</article>';
}
function unitPlayer(name) {
  if (!name) return '';
  return '<span class="tagName" style="color:' + colorOf(name) + '">' + escapeHtml(name) + '</span>';
}
function unitPlayersV3(u) {
  return [...new Set((u.members || [u]).map(m => m.actor || m.initiator))].map(unitPlayer).join(' · ');
}
function unitStatusesV3(u) {
  if (u.kind === 'deception') return '';
  const counts = new Map();
  for (const member of u.members || [u]) counts.set(member.status, (counts.get(member.status) || 0) + 1);
  return [...counts].map(([status,count]) => '<span class="turnTagStatus researchTone-' +
    unitTone({...u,status}) + '">' + (u.members?.length > 1 ? count + ' ' : '') +
    escapeHtml(pretty(status)) + '</span>').join(' · ');
}
function unitEvidenceV3(u) {
  const found = new Map();
  const add = cites => (cites || []).forEach(c => {
    if (c?.span == null) return;
    const key = JSON.stringify([c.span,c.quote,c.field_path,c.field_value]);
    if (!found.has(key)) found.set(key,c);
  });
  add([u.passage]);
  if (u.kind === 'deception') {
    add(unitCitations(u));
  } else if (u.kind === 'statement') {
    add(u.comparison); add(u.deliberate_intent); add(u.context); add(u.mentions);
  } else {
    for (const member of u.members || [u]) {
      add([member.passage]); add(member.establishment);
      (member.proposals || []).forEach(p => { add(p.evidence); (p.responses || []).forEach(r => add(r.evidence)); });
      (member.revisions || []).forEach(r => { add(r.evidence); add(r.authorization);
        (r.responses || []).forEach(p => add(p.evidence)); });
      add(member.trigger); add(member.outcome); add(member.mentions);
      (member.participant_history || []).forEach(h => add(h.evidence));
      (member.status_history || []).forEach(h => {
        add(h.evidence);
        if (h.cutoff_span != null && ![...found.values()].some(c => c.span === h.cutoff_span)) {
          add([{span:h.cutoff_span,resolved:spanById.has(h.cutoff_span)}]);
        }
      });
    }
  }
  const offset = c => {
    const text = spanById.get(c.span)?.text;
    const pos = text && c.quote ? text.indexOf(c.quote) : -1;
    return pos < 0 ? Infinity : pos;
  };
  const bySpan = new Map();
  for (const cite of [...found.values()].sort((a,b) => a.span - b.span || offset(a) - offset(b))) {
    if (!bySpan.has(cite.span)) bySpan.set(cite.span, []);
    bySpan.get(cite.span).push(cite);
  }
  return [...bySpan].map(([span,cites]) =>
    '<div class="unitEvidenceItem" id="tagEvidenceSpan-' + span + '">' +
    evidenceQuote(u,cites[0],cites) + '</div>').join('');
}
function jumpToTagEvidence(span) {
  const evidence = el("tagEvidence");
  if (!evidence) return;
  state.openTagEvidence = true;
  evidence.open = true;
  requestAnimationFrame(() => {
    const target = el("tagEvidenceSpan-" + span);
    if (!target) return;
    target.scrollIntoView({block:"center"});
    target.classList.remove("evidenceFlash");
    void target.offsetWidth;
    target.classList.add("evidenceFlash");
    setTimeout(() => target.classList.remove("evidenceFlash"), 1600);
  });
}
function commitmentLinesV3(u, grouped = false) {
  const lines = [];
  const add = (span, status, actor) => {
    const text = '<strong>' + escapeHtml(pretty(status)) + '</strong> · ' + unitPlayer(actor);
    if (span != null) lines.push({span, text, order: lines.length});
  };
  for (const member of u.members || [u]) {
    let last = null;
    const proposerWithProposal = (member.role ? member.role === 'proposer' :
      (member.status_history || [])[0]?.status === 'proposed');
    for (const event of member.status_history || []) {
      if (event.status === last) continue;
      // The shared proposal is one event by its author, even in older ledgers
      // that copied it into every invitee's status history.
      if ((u.members || grouped) && event.status === 'proposed' && member.role && member.role !== 'proposer') {
        last = event.status;
        continue;
      }
      if (!(proposerWithProposal && ['made','agreed'].includes(event.status))) {
        add(event.cutoff_span ?? event.evidence?.[0]?.span, event.status, member.actor);
      }
      last = event.status;
    }
  }
  return lines.sort((a,b) => a.span - b.span || a.order - b.order).map(line =>
    '<div class="tagDevelopment"><span class="tagDevelopmentTime">' + escapeHtml(v3Time(line.span)) +
    '</span><button class="chip" onclick="jumpToTagEvidence(' + line.span + ')">[' + line.span +
    ']</button><span>' + line.text + '</span></div>').join('');
}
function undertakingDetailV3(u, grouped = false) {
  const source = spanById.get(u.passage?.span);
  const developments = u.kind === 'commitment' ? commitmentLinesV3(u,grouped) : '';
  return '<p class="tagExplanation">' + richText(u.explanation || '') + '</p>' +
    (u.scope_explanation ? '<p>Commitment relevance: ' + richText(u.scope_explanation) + '</p>' : '') +
    (u.contradictions || []).map(b => '<p><strong>Contradiction evidence · ' + escapeHtml(pretty(b.basis)) +
      '</strong>: ' + richText(b.explanation) + '</p>').join('') +
    (u.participant_history?.length ? '<details><summary>Participant roles and responses</summary>' +
      u.participant_history.map(p => '<p>' + escapeHtml(p.actor + ' · ' + pretty(p.role) + ' · ' +
        pretty(p.response) + ' · ' + v3Time(p.cutoff_span)) + '</p>').join('') + '</details>' : '') +
    (u.needs_review ? '<p class="researchCaution">Technical review: ' + escapeHtml((u.issues || []).join('; ')) + '</p>' : '') +
    (u.kind === 'commitment' && !u.established ? '<p class="tagUnestablished">No undertaking established</p>' : '') +
    (u.reason ? '<p><strong>Reason:</strong> ' + richText(u.reason) + '</p>' : '') +
    (u.uncertainty ? '<p class="researchCaution">' + richText(u.uncertainty) + '</p>' : '') +
    (u.passage?.span != null ? '<div class="tagSource"><button class="chip" onclick="jumpToTagEvidence(' +
      u.passage.span + ')">[' + u.passage.span + ']</button> ' +
      escapeHtml(source ? v3Time(source.span) : 'Source passage') + '</div>' : '') +
    (developments ? '<div class="tagDevelopments">' + developments + '</div>' : '') +
    (u.revisions?.length ? '<details class="tagAssessments"><summary>Changes to terms</summary>' +
      u.revisions.map(r => '<p><strong>' +
        (r.effective === true ? 'Effective' : r.effective === false ? 'Not effective' : escapeHtml(pretty(r.status))) +
        '</strong>: ' + richText(r.terms) + (r.explanation ? ' — ' + richText(r.explanation) : '') + ' ' +
        (r.evidence || []).map(c => '<button class="chip" onclick="jumpToTagEvidence(' + c.span + ')">[' +
          c.span + ']</button>').join('') + '</p>').join('') + '</details>' : '');
}
function unitDetailV3(u) {
  const evidence = unitEvidenceV3(u), verification = verifyControl(u);
  return '<div class="tagExpanded">' +
    (u.members?.length > 1 ? '<div class="tagUndertakings">' + u.members.map(m =>
      '<details class="tagUndertaking"' + (state.unitDrawer?.undertakings?.has(m.id) ? ' open' : '') +
      ' ontoggle="if(state.unitDrawer?.key===\'' + unitKey(u) + '\' && state.unitDrawer.undertakings) this.open ? state.unitDrawer.undertakings.add(\'' + m.id +
      '\') : state.unitDrawer.undertakings.delete(\'' + m.id + '\')"><summary>' +
      '<span class="tagUndertakingMain">' + unitPlayer(m.actor) +
      '<span class="tagUndertakingTerms">' + richText(unitTitle(m)) + '</span></span>' +
      '<span class="tagUndertakingStatus">' + unitStatusesV3(m) +
      (m.needs_review ? ' · <span class="tagReview">Needs review</span>' : '') + '</span></summary>' +
      undertakingDetailV3(m,true) + '</details>').join('') + '</div>' : undertakingDetailV3(u.members?.[0] || u)) +
    (evidence ? '<details class="tagEvidence" id="tagEvidence"' +
      (state.openTagEvidence ? ' open' : '') +
      ' ontoggle="if(state.unitDrawer?.key===\'' + unitKey(u) +
      '\') state.openTagEvidence=this.open"><summary>Evidence</summary>' +
      evidence + '</details>' : '') +
    (verification ? '<footer class="tagFooter">' + verification + '</footer>' : '') + '</div>';
}
function clearResearchFilters() {
  Object.assign(state.filters, {actor:"", code:"", status:"", basis:"", kind:"", validOnly:false});
  renderFilters(); renderTimeline();
}
function labeledEvidence(u) {
  const walk = (value, path) => {
    if (!value || typeof value !== "object") return "";
    if ("span" in value && "quote" in value) return '<p class="kindlabel">' + escapeHtml(path) + '</p>' + evidenceQuote(u, value);
    return Object.entries(value).map(([key, child]) => walk(child, path ? path + " / " + pretty(key) : pretty(key))).join("");
  };
  return walk(u, "");
}
function unitCard(u, expanded = false) {
  if (isTwoTagSchema()) return unitCardV3(u, expanded);
  const title = unitTitle(u);
  let details = "";
  if (u.kind === "episode") {
    const termLabels = {action:"Promise", target:"Target", condition:"Condition", start:"Starts", deadline:"Deadline", restraint:"Promise not to act"};
    details = '<dl class="researchTerms">' + Object.entries(u.terms).map(([k,v]) => '<dt>' + escapeHtml(termLabels[k] || pretty(k)) +
      '</dt><dd>' + escapeHtml(v == null ? "Not specified" : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)) + '</dd>').join("") + '</dl>' +
      '<p>Counterparties: ' + escapeHtml(u.counterparties.join(", ") || "Not specified") +
      ' · Third-party targets: ' + escapeHtml(u.third_party_targets.join(", ") || "None stated") + '</p>' +
      (u.agreement_id ? linkedUnitButton(u, "agreement", u.agreement_id, "Related proposal / agreement") : "") +
      '<p>Assessment window: <button class="chip" onclick="openInterval(' + u.interval.start_span + ',' + u.interval.end_span + ')">[' +
      u.interval.start_span + '–' + u.interval.end_span + ']</button> ' + escapeHtml(u.interval.endpoint) +
      ' · ' + (u.interval.complete ? "Complete window" : "Incomplete window") + '</p>' +
      '<p>Action source: ' + escapeHtml(pretty(u.action_provenance)) + '</p>' +
      u.revisions.map(r => '<div class="researchRevision"><strong>' + escapeHtml(r.author + " · " + pretty(r.status)) +
        '</strong><p>' + escapeHtml(r.terms) + '</p>' +
        (r.responses || []).map(p => '<p>' + escapeHtml(p.player + ': ' + pretty(p.status)) + '</p>').join("") + '</div>').join("");
  } else if (u.kind === "agreement") {
    details = u.proposals.map(p => '<section class="researchRevision"><strong>' + escapeHtml(p.author) +
      '</strong><p>' + escapeHtml(p.terms) + '</p>' + p.responses.map(r => '<p>' +
      escapeHtml(r.player + ": " + pretty(r.status)) + '</p>').join("") + '</section>').join("");
  } else {
    details = '<p>Evidence basis: ' + escapeHtml(pretty(u.basis || "unknown")) + '</p>' +
      (u.episode_id ? linkedUnitButton(u, "episode", u.episode_id, "Related commitment") : "");
  }
  const key = escapeHtml(JSON.stringify((EVAL.version || "") + ':' + unitKey(u)));
  const cite = unitCitations(u).find(c => spanById.has(c.span)), span = cite && spanById.get(cite.span);
  const time = span ? gameTimeLabel(span.turn, span.round) : "";
  return '<details class="card researchCard researchUnit researchTone-' + unitTone(u) + '"' +
    (expanded || state.openUnits.has((EVAL.version || "") + ':' + unitKey(u)) ? ' open' : '') +
    ' ontoggle="this.open ? state.openUnits.add(' + key + ') : state.openUnits.delete(' + key + ')">' +
    '<summary><span class="researchRow"><span class="researchActor" style="color:' + colorOf(u.actor || u.initiator) + '">' +
    escapeHtml(tagOf(u.actor || u.initiator)) + '</span><span class="researchTitle">' + escapeHtml(title) +
    '</span><span class="researchStatus">' + escapeHtml(unitStatus(u)) + '</span></span>' +
    '<span class="researchMeta">' + escapeHtml([unitType(u), time, annotatorLabel(u.annotator)].filter(Boolean).join(' · ')) +
    (u.kind === 'claim' && u.basis ? ' · ' + escapeHtml(pretty(u.basis)) : '') +
    (u.kind === 'agreement' && u.proposals.length > 1 ? ' · latest of ' + u.proposals.length + ' proposals' : '') +
    (u.codes?.includes("deceptive_communication") ? ' · deceptive communication' : '') +
    (u.valid ? '' : ' · needs review · excluded from counts') + (u.verified ? ' · ✓ reviewed' : '') + '</span></summary>' +
    '<div class="researchBody"><div class="researchCodes">' + (u.codes || []).map(c => '<span class="pill">' + escapeHtml(pretty(c)) + '</span>').join("") +
    '</div><p>' + escapeHtml(u.explanation || "") + '</p>' + details +
    (u.uncertainty ? '<p class="researchCaution">Alternative reading: ' + escapeHtml(u.uncertainty) + '</p>' : "") +
    (u.issues?.length ? '<div class="researchCaution">Excluded from counts: ' + u.issues.map(escapeHtml).join("; ") + '</div>' : "") +
    '<details class="researchEvidence"><summary>Evidence · ' + unitCitations(u).length + ' citations</summary>' +
    labeledEvidence(u) + '</details><p class="researchId">Internal reference: ' + escapeHtml(u.id) +
    ' · identifies this record within this evaluator’s run; not a codebook code.</p>' + verifyControl(u) + '</div></details>';
}
function verifyControl(u) {
  if (!VERIFICATIONS_URL) return u.verified ? '<p>✓ Reviewed</p>' : "";
  return '<button class="verifybtn ' + (u.verified ? "on" : "") + '" onclick="setUnitVerified(\'' +
    unitKey(u) + '\',' + !u.verified + ')">' + (u.verified ? "✓ Reviewed" : "Mark reviewed") + '</button>';
}
function comparisonHtml() {
  if (!state.filters.showCommentary) return '<p class="empty">Enable annotations to compare research assessments.</p>';
  const members = (EVAL.members || []).filter(m => state.filters.annotators.has(m.index));
  const rows = new Map();
  for (const u of allUnits().filter(u => unitVisible(u) && u.kind !== "agreement")) {
    const c = u.passage || u.undertaking || u.message, key = JSON.stringify([u.kind,u.actor,c.span,c.quote,u.basis,u.proposition,u.relevant_time]);
    if (!rows.has(key)) rows.set(key, {unit:u, votes:{}});
    (rows.get(key).votes[u.annotator] ??= []).push(u);
  }
  return '<p class="statNote">Compare assessments at the same quoted source. Multiple units in one cell indicate a split that needs review. Missing does not mean agreement or honesty.</p><div class="researchTable"><table class="statsTable"><thead><tr><th>Source / unit</th>' +
    members.map(m => '<th>' + escapeHtml(m.label) + '</th>').join("") + '</tr></thead><tbody>' +
    [...rows.values()].map(r => '<tr><td>' + unitPill(r.unit) + '</td>' + members.map(m =>
      '<td>' + (r.votes[m.index] || []).map(u => escapeHtml(pretty(u.status)) +
        (u.valid ? "" : " · invalid") + '<br>' + (u.codes || []).map(pretty).join(", ")).join("<hr>") +
      (!(r.votes[m.index]?.length) ? "Not extracted / missing" : "") + '</td>').join("") + '</tr>').join("") + '</tbody></table></div>';
}
function toggleExpand(turn) { state.expanded.has(turn) ? state.expanded.delete(turn) : state.expanded.add(turn); focusTurn(turn); renderTimeline(); }
el("expandAll").onclick = () => {
  if (state.mode === "research") allUnits().filter(unitVisible).forEach(u => state.openUnits.add((EVAL.version || "") + ':' + unitKey(u)));
  else turns.forEach(t => state.expanded.add(t));
  renderTimeline();
};
el("collapseAll").onclick = () => {
  if (state.mode === "research") state.openUnits.clear();
  else state.expanded.clear();
  renderTimeline();
};
const EVIDENCE_COLLAPSE_LIMIT = 280;
function evidenceQuote(t, e, citations = [e]) {
  const s = spanById.get(e.span);
  const chip = `<button class="chip" onclick="openLineDrawer(${e.span})" title="open in the raw trace">[${e.span}]</button>`;
  if (!s || !e.resolved) return `<div class="evquote">${chip} <span class="mono" style="font-size:9.5px;color:var(--faint)">not in the trace (unresolved citation)</span></div>`;
  const [kindLabel, kindColor] = KINDS[s.layer] || KINDS.event;
  const key = `${unitKey(t)}:${e.span}`;
  const long = s.text.length > EVIDENCE_COLLAPSE_LIMIT;
  const collapsible = long && citations.length === 1;
  const open = state.openEvidence.has(key);
  const textCitations = citations.filter(cite => !cite.field_path);
  const fieldCitations = citations.filter(cite => cite.field_path);
  const evidence = [
    textCitations.length ? evidenceTextGroup(s, textCitations, collapsible && !open) : '',
    ...fieldCitations.map(cite => structuredEvidenceText(s,cite)),
  ].filter(Boolean).join(' ');
  return `<div class="evquote" style="border-left-color:${kindColor}">
    <div class="evhead">
      ${chip}
      ${s.layer === 'action' ? '' : `<span class="kindlabel">${kindLabel}</span>`}
      ${e.temporal_relationship ? '<span class="kindlabel">' + escapeHtml(pretty(e.temporal_relationship)) + '</span>' : ""}
      ${s.player && s.layer !== 'action' ? `<span class="mono" style="font-size:9px;font-weight:600;color:${colorOf(s.player)}">${escapeHtml(s.player)}</span>` : ""}
      ${isTwoTagSchema() ? `<span class="mono" style="font-size:9px;color:var(--faint)">${escapeHtml(v3Time(e.span))}</span>` :
        s.turn !== t.turn ? `<span class="mono" style="font-size:9px;color:var(--faint)">turn ${s.turn}</span>` : ""}
      ${collapsible ? `<button class="evmore" onclick="toggleEvidence('${key}')">${open ? "▴ collapse" : "▸ show full line"}</button>` : ""}
    </div>
    <div class="evtext">${evidence}</div>
  </div>`;
}

function structuredEvidenceText(source, cite) {
  const path = cite.field_path || [];
  if (source.layer === 'action' && path[0] === 'action') return '<mark>' + richText(source.text) + '</mark>';
  if (path[0] === 'board' && path.length === 2 && Array.isArray(cite.resolved_field_value)) {
    const [owner, armies] = cite.resolved_field_value;
    return '<mark>' + escapeHtml(placeName(path[1]) + ': ' + armies + ' armies · ' + owner) + '</mark>';
  }
  return '<mark>' + escapeHtml(path.join(' / ') + ' = ' + JSON.stringify(cite.resolved_field_value)) + '</mark>';
}

function evidenceText(s, e, collapsed) {
  return evidenceTextGroup(s, [e], collapsed);
}

function evidenceTextGroup(s, citations, collapsed) {
  const quotes = [...new Set(citations.filter(cite => cite.quote_matched && cite.quote).map(cite => cite.quote))];
  if (collapsed) {
    const excerpts = quotes.filter(quote => quote.length <= EVIDENCE_COLLAPSE_LIMIT);
    if (excerpts.length) return '…' + excerpts.map(quote => '<mark>' + richText(quote) + '</mark>').join(' … ') + '…';
    return richText(s.text.slice(0, EVIDENCE_COLLAPSE_LIMIT)) + "…";
  }
  const ranges = quotes.map(quote => [s.text.indexOf(quote), s.text.indexOf(quote) + quote.length])
    .filter(([start]) => start >= 0).sort((a,b) => a[0] - b[0]);
  if (!ranges.length) return richText(s.text);
  const merged = [];
  for (const [start,end] of ranges) {
    if (merged.length && start <= merged.at(-1)[1]) merged.at(-1)[1] = Math.max(end, merged.at(-1)[1]);
    else merged.push([start,end]);
  }
  let cursor = 0, html = '';
  for (const [start,end] of merged) {
    html += richText(s.text.slice(cursor,start)) + '<mark>' + richText(s.text.slice(start,end)) + '</mark>';
    cursor = end;
  }
  return html + richText(s.text.slice(cursor));
}

function toggleEvidence(key) {
  state.openEvidence.has(key) ? state.openEvidence.delete(key) : state.openEvidence.add(key);
  renderTimeline(); renderPanels();
}
