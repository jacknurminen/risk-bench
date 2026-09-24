/* ================= LEFT RAIL ================= */
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
          <div style="display:flex;align-items:center;gap:7px">
            <span class="ptag" style="color:${COLORS[seat]}">${tagOf(p.name)}</span>
            <span class="presult" style="color:${rc};background:color-mix(in srgb, ${rc} 13%, #fff)">${rl}</span>
            ${hasNotes ? `<button class="iconbtn" onclick="openNotes('${p.name}')" title="all private notes" style="margin-left:auto;font-size:12px">📝</button>` : ""}
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
  select("statusFilter", ["open","fulfilled","violated","released","not_triggered","unassessable","assessable","unclear","insufficient_evidence"], state.filters.status, "statuses");
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
  thought: ["private reasoning", "var(--deceptive)"], message: ["delivered message", "var(--blue)"],
  action: ["applied action", "#8a7b57"], event: ["engine event", "#9aa3ae"], note: ["private note", "#b07d2e"],
};
function renderTimeline() {
  const root = el("timeline");
  if (state.mode === "research") root.innerHTML = researchHtml();
  else if (state.mode === "compare") root.innerHTML = comparisonHtml();
  else root.innerHTML = turns.map(turnCard).join("");
  el("turnCount").textContent = turns.length + " turns";
  el("subStat").textContent = EVAL.has_evaluation
    ? allUnits().filter(u => u.kind === "episode").length + " commitment assessments · " + allUnits().filter(u => u.kind === "claim").length + " claim assessments"
    : "No evaluation yet";
  show(index);
}
function turnCard(turn) {
  const setup = isSetupTurn(turn), actor = turnActor(turn), accent = setup ? "var(--mute)" : colorOf(actor);
  const units = state.filters.showCommentary ? allUnits().filter(u => unitVisible(u) && u.turn === turn) : [];
  const notes = state.filters.showCommentary && EVAL.coverage?.source_matches
    ? (EVAL.live_commentary || []).filter(note => note.turn === turn && state.filters.annotators.has(note.evaluator_index)) : [];
  const finalNotes = state.filters.showCommentary && EVAL.coverage?.source_matches
    ? (EVAL.live_final || []).filter(note => note.turn === turn && state.filters.annotators.has(note.evaluator_index)) : [];
  return '<article class="tcard" data-turn="' + turn + '" style="border-left-color:' + accent + '">' +
    '<button class="thead" onclick="toggleExpand(' + turn + ')"><span class="tbadge" style="background:' + accent + '">' +
    (setup ? "⚑" : initialsOf(actor)) + '</span><span class="tmeta"><span class="line1">' +
    '<span class="ttag">' + (setup ? "Setup placement" : escapeHtml(tagOf(actor))) + '</span><span class="tturn">' +
    gameTimeLabel(turn, roundByTurn[turn]) + '</span></span><span class="tsummary">' + richText(turnSummary(turn)) +
    '</span><span class="turnHighlights">' + turnHighlights(turn).map(h => '<span class="moveChip">' + h.label + '</span>').join("") +
    '</span></span><span class="chev">⌄</span></button>' +
    (units.length ? '<div class="tpills">' + units.map(unitPill).join("") + '</div>' : "") +
    notes.map(note => '<div class="commentary"><span class="live">research · ' +
      escapeHtml(annotatorLabel(note.evaluator_index)) + '</span> <span class="ctext">' +
      escapeHtml(note.text) + '</span></div>').join("") +
    finalNotes.map(note => '<div class="commentary"><span class="live">final review · ' +
      escapeHtml(annotatorLabel(note.evaluator_index)) + '</span> <span class="ctext">' +
      escapeHtml(note.text) + '</span></div>').join("") +
    (state.expanded.has(turn) ? expandedBody(turn) : "") + '</article>';
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
  return '<button class="pill researchTone-' + unitTone(u) + '" title="' + escapeHtml(unitTitle(u)) + '" onclick="openUnitDrawer(\'' + unitKey(u) + '\')">' +
    escapeHtml(unitType(u) + " · " + tagOf(u.actor || u.initiator) + " · " + unitStatus(u)) +
    (u.valid ? "" : " · needs review") + '</button>';
}
function unitType(u) { return {agreement:"Proposal / agreement", episode:"Commitment", claim:"Claim"}[u.kind]; }
function unitTitle(u) {
  return u.kind === "episode" ? u.terms.action : u.kind === "claim" ? u.proposition :
    (u.proposals.at(-1)?.terms || "Proposal terms not recorded");
}
function unitStatus(u) {
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
    const c = u.undertaking || u.message, key = JSON.stringify([u.kind,u.actor,c.span,c.quote,u.basis]);
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
function evidenceQuote(t, e) {
  const s = spanById.get(e.span);
  const chip = `<button class="chip" onclick="openLineDrawer(${e.span})" title="open in the raw trace">[${e.span}]</button>`;
  if (!s || !e.resolved) return `<div class="evquote">${chip} <span class="mono" style="font-size:9.5px;color:var(--faint)">not in the trace (unresolved citation)</span></div>`;
  const [kindLabel, kindColor] = KINDS[s.layer] || KINDS.event;
  const key = `${unitKey(t)}:${e.span}`;
  const long = s.text.length > EVIDENCE_COLLAPSE_LIMIT;
  const open = state.openEvidence.has(key);
  return `<div class="evquote" style="border-left-color:${kindColor}">
    <div class="evhead">
      ${chip}
      <span class="kindlabel">${kindLabel}</span>
      ${e.temporal_relationship ? '<span class="kindlabel">' + escapeHtml(pretty(e.temporal_relationship)) + '</span>' : ""}
      ${s.player ? `<span class="mono" style="font-size:9px;font-weight:600;color:${colorOf(s.player)}">${tagOf(s.player)}</span>` : ""}
      ${s.turn !== t.turn ? `<span class="mono" style="font-size:9px;color:var(--faint)">turn ${s.turn}</span>` : ""}
      ${long ? `<button class="evmore" onclick="toggleEvidence('${key}')">${open ? "▴ collapse" : "▸ show full line"}</button>` : ""}
    </div>
    <div class="evtext">${evidenceText(s, e, long && !open)}</div>
  </div>`;
}

function evidenceText(s, e, collapsed) {
  const quoted = e.quote_matched && e.quote;
  if (collapsed) {
    if (quoted && e.quote.length <= EVIDENCE_COLLAPSE_LIMIT) return `…<mark>${escapeHtml(e.quote)}</mark>…`;
    return escapeHtml(s.text.slice(0, EVIDENCE_COLLAPSE_LIMIT)) + "…";
  }
  const html = escapeHtml(s.text);
  return quoted ? html.replace(escapeHtml(e.quote), match => `<mark>${match}</mark>`) : html;
}

function toggleEvidence(key) {
  state.openEvidence.has(key) ? state.openEvidence.delete(key) : state.openEvidence.add(key);
  renderTimeline(); renderPanels();
}
