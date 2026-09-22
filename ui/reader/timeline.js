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
  // only offer axis chips for axes that actually occur in the present tags; with no tags at all
  // (nothing evaluated or annotated yet) the whole axis filter disappears rather than sit inert
  const presentAxes = new Set(allTags().map(t => axisOf(t.behavior)));
  if (state.filters.showCommentary)
    GAME.commentary.forEach(c => (c.tags || []).forEach(t => presentAxes.add(axisOf(t.behavior))));
  const offeredAxes = AXES.filter(([key]) => presentAxes.has(key));
  el("axisSection").style.display = offeredAxes.length ? "" : "none";
  el("axisRow").innerHTML = offeredAxes.map(([key, label, color]) => {
    const on = state.filters.axes.has(key);
    return `<button class="axischip" onclick="toggleAxis('${key}')" style="${on ? `border-color:${color};background:color-mix(in srgb, ${color} 12%, #fff);color:var(--ink)` : ""}">
      <span class="swatch" style="background:${color}"></span>${label}</button>`;
  }).join("");
  el("confSeg").innerHTML = [[1, "≥1"], [2, "≥2"], [3, "3"]].map(([n, l]) =>
    `<button class="${state.filters.minConf === n ? "on" : ""}" onclick="setMinConf(${n})">${l}</button>`).join("");
  const counts = {}; allTags().forEach(t => counts[t.annotator] = (counts[t.annotator] || 0) + 1);
  el("annList").innerHTML = annotators().map(a => {
    const on = state.filters.annotators.has(a.index);
    return `<button class="annrow ${on ? "on" : "off"}" onclick="toggleAnnotator(${a.index})">
      <span class="swatch" style="width:9px;height:9px;border-radius:2px;background:${annotatorColor(a.index)}"></span>
      <span class="mono" style="font-size:11.5px;flex:1;text-align:left">${escapeHtml(a.label)}</span>
      <span class="mono" style="font-size:10px;color:var(--faintest)">${counts[a.index] || 0}</span>
      <span class="checkbox ${on ? "on" : ""}"></span></button>`;
  }).join("");
  el("privToggle").className = "toggle" + (state.filters.showPrivate ? " on" : "");
  el("privToggle").querySelector(".switch").className = "switch" + (state.filters.showPrivate ? " on" : "");
  el("privToggle").querySelector(".switch").style.background = state.filters.showPrivate ? "var(--deceptive)" : "";
  el("commToggle").className = "toggle" + (state.filters.showCommentary ? " on" : "");
  el("commToggle").querySelector(".switch").className = "switch" + (state.filters.showCommentary ? " on" : "");
  el("commToggle").querySelector(".switch").style.background = state.filters.showCommentary ? "var(--blue)" : "";
}
function toggleAxis(key) { const s = state.filters.axes; s.has(key) ? (s.size > 1 && s.delete(key)) : s.add(key); renderFilters(); renderTimeline(); }
function setMinConf(n) { state.filters.minConf = n; renderFilters(); renderTimeline(); }
function toggleAnnotator(idx) { const s = state.filters.annotators; s.has(idx) ? s.delete(idx) : s.add(idx); renderFilters(); renderTimeline(); }
el("privToggle").onclick = () => { state.filters.showPrivate = !state.filters.showPrivate; renderFilters(); renderTimeline(); };
el("commToggle").onclick = () => { state.filters.showCommentary = !state.filters.showCommentary; renderFilters(); renderTimeline(); };

/* ================= TIMELINE ================= */
const KINDS = {
  thought: ["thinks · private", "var(--deceptive)"], message: ["public · table-talk", "var(--blue)"],
  action: ["board action", "#8a7b57"], event: ["engine", "#9aa3ae"], note: ["memory · private", "#b07d2e"],
};
function tagsForTurn(turn) { return allTags().filter(t => t.turn === turn && tagVisible(t)); }

function pill(t) {
  const [vc, vt] = valenceStyle(t.behavior);
  const ac = annotatorColor(t.annotator);
  return `<span class="pill" onclick="openTagDrawer(${t.annotator}, '${t.behavior}', ${t.turn})"
    style="background:${vt};color:${vc};border-color:${t.verified ? "var(--coop)" : "transparent"}">
    <span class="swatch" style="background:${AXIS_COLOR[axisOf(t.behavior)]}"></span>${t.behavior}
    <span class="mono" style="font-size:9px;color:${colorOf(t.player)};font-weight:600">${tagOf(t.player)}</span>
    <span class="mono" style="font-size:9px;opacity:.7">${t.confidence}/3</span>
    ${t.verified ? '<span class="mono" style="font-size:9px;color:var(--coop);font-weight:700" title="human-verified">✓</span>' : ""}
    <span style="width:14px;height:14px;border-radius:3px;background:${ac};color:#fff;font-family:var(--font-mono);font-size:8px;font-weight:600;display:inline-flex;align-items:center;justify-content:center">${annotatorBadge(t.annotator)}</span>
  </span>`;
}

function renderTimeline() {
  if (state.mode === "compare") { renderCompare(); return; }
  const container = el("timeline");
  const visible = turns.filter(turn => state.filters.annotators.size || true);
  container.innerHTML = visible.map(turn => turnCard(turn)).join("");
  updateSubBar();
  show(index); // re-apply focus highlight
}

function turnCard(turn) {
  const setup = isSetupTurn(turn);
  const actor = turnActor(turn) || GAME.players[0].name;
  const accent = setup ? "var(--mute)" : colorOf(actor);  // setup belongs to no seat — a neutral accent
  const expanded = state.expanded.has(turn);
  const pills = tagsForTurn(turn);
  const hasGap = pills.some(t => valenceOf(t.behavior) === "deceptive");
  // the live-commentary toggle also hides the panel's read (tag pills + deception badge) so a human can read unbiased
  const showTags = state.filters.showCommentary;
  const commentary = state.filters.showCommentary ? GAME.commentary.filter(c => c.turn === turn) : [];
  return `<article class="tcard" data-turn="${turn}" style="border-left-color:${accent}">
    <button class="thead" onclick="toggleExpand(${turn})">
      <span class="tbadge" style="background:${accent}">${setup ? "⚑" : initialsOf(actor)}</span>
      <span class="tmeta">
        <span class="line1">
          <span class="ttag" style="color:${accent}">${setup ? "setup placement" : tagOf(actor)}</span>
          <span class="tturn">${gameTimeLabel(turn, roundByTurn[turn])}</span>
          ${hasGap && showTags ? `<span class="gapbadge">⚡ deception gap</span>` : ""}
        </span>
        <span class="tsummary">${richText(turnSummary(turn))}</span>
        <span class="turnHighlights">${turnHighlights(turn).map(h => `<span class="moveChip ${h.kind}">${h.label}</span>`).join("")}</span>
      </span>
      <span class="chev" style="transform:rotate(${expanded ? 180 : 0}deg)">⌄</span>
    </button>
    ${showTags && pills.length ? `<div class="tpills">${pills.map(pill).join("")}</div>` : ""}
    ${expanded ? expandedBody(turn) : ""}
    ${commentary.length ? commentaryBlock(commentary) : ""}
  </article>`;
}

function expandedBody(turn) {
  const lines = (spansByTurn.get(turn) || []).filter(s =>
    (state.filters.showPrivate || (s.layer !== "thought" && s.layer !== "note"))
    && !(s.layer === "event" && eventEchoesAction(s.text))
    && !(s.layer === "note" && lastNoteSpanByPlayer[s.player] !== s.span));
  const liveBySpan = commentatorTagsBySpan(turn);
  // Several entries can come out of one committed transition, so a seam is only offered where the
  // cursor actually changes — one per forkable boundary rather than one per line.
  const rows = [];
  let cursor = null;
  lines.forEach(s => {
    if (s.cursor && s.cursor.before !== cursor?.before) rows.push(forkSeparator(s.cursor.before));
    cursor = s.cursor || cursor;
    rows.push(traceLine(turn, s, liveBySpan.get(s.span) || []));
  });
  // The final boundary is the game's head; later turns open with it as their own leading seam.
  if (cursor && turn === turns[turns.length - 1]) rows.push(forkSeparator(cursor.after));
  return `<div class="expand">${rows.join("")}</div>`;
}
// engine events that merely restate the board action above them (place/fortify), or the turn header
// the card already shows — hidden from the timeline, but still present in the raw-trace evidence drawer
function eventEchoesAction(text) { return /\bplaced\b|\bfortified\b/.test(text) || /^Turn \d+ started/.test(text); }

/* A commentator tags a (turn, player) pair, not a line, so its bubble is anchored to the tagged
   player's first public span that turn — the message or action a spectator actually saw. Tags whose
   player never spoke or acted that turn stay visible in the commentary block only. */
function commentatorTagsBySpan(turn) {
  const bySpan = new Map();
  if (!state.filters.showCommentary) return bySpan;
  const lines = spansByTurn.get(turn) || [];
  const anchorOf = player =>
    (lines.find(s => s.player === player && (s.layer === "message" || s.layer === "action")) || {}).span;
  GAME.commentary.filter(c => c.turn === turn).forEach(c => (c.tags || []).forEach(t => {
    if (!state.filters.axes.has(axisOf(t.behavior))) return;
    const span = anchorOf(t.player);
    if (span == null) return;
    if (!bySpan.has(span)) bySpan.set(span, []);
    bySpan.get(span).push({ ...t, live: true, commentator: c.commentator, commentator_index: c.commentator_index });
  }));
  return bySpan;
}

function traceLine(turn, s, liveAnno) {
  let [kindLabel, kindColor] = KINDS[s.layer] || KINDS.event;
  if (s.layer === "action" || s.layer === "event") {
    const kind = /\bconquered\b/.test(s.text) ? ["conquest", "var(--coop)"]
      : /\bELIMINATED\b/.test(s.text) ? ["elimination", "var(--adversarial)"]
      : /\battacks?\b|\battacked\b/.test(s.text) && !/ends the attack/.test(s.text) ? ["attack", "var(--adversarial)"]
      : /\bplaces?\b|\bplaced\b/.test(s.text) ? ["placement", "var(--coop)"]
      : /\bfortifies\b|\bfortified\b/.test(s.text) ? ["fortification", "var(--blue)"] : null;
    if (kind) [kindLabel, kindColor] = kind;
  }
  const isMsg = s.layer === "message", isPrivate = s.layer === "thought" || s.layer === "note";
  const anno = [...tagsForTurn(turn).filter(t => t.evidence?.some(e => e.span === s.span)), ...liveAnno];
  const annoOpen = state.openAnno.has(turn + ":" + s.span);
  const bodyColor = s.layer === "event" ? "var(--mute)" : (isPrivate ? "#57534a" : "var(--ink-soft)");
  const dots = anno.slice(0, 5).map((t, i) => `<span class="annodot" style="background:${valenceStyle(t.behavior)[0]};margin-left:${i ? -3 : 0}px"></span>`).join("");
  const content = `<div>
    <div class="traceline" onclick="lineClick(event, ${s.step})" title="wind the board to this moment">
      <button class="chip" onclick="chipClick(${s.span})" title="open in the raw trace">[${s.span}]</button>
      <span class="kindbar" style="background:${kindColor}"></span>
      <span style="flex:1;min-width:0">
        <span class="kindlabel">${kindLabel}${s.inherited ? " · inherited" : ""}${s.origin === "human" ? " · intervention" : ""}</span>
        ${s.player ? `<span class="tracewho" style="color:${colorOf(s.player)}">${tagOf(s.player)}</span>` : ""}
        <span class="tracetext ${isMsg ? "msg" : "mono"}" style="color:${bodyColor}">${richText(s.text)}</span>
      </span>
      ${anno.length ? `<button class="iconbtn" onclick="toggleAnno(${turn}, ${s.span})" title="annotations on this line" style="display:flex;align-items:center;gap:6px">
        <span class="annodots">${dots}</span><span class="chev" style="font-size:13px;transform:rotate(${annoOpen ? 180 : 0}deg)">⌄</span></button>` : ""}
    </div>
    ${annoOpen ? `<div style="margin:3px 0 9px 42px;display:flex;flex-direction:column;gap:6px">${anno.map(annoCard).join("")}</div>` : ""}
  </div>`;
  if (!isPrivate) return content;
  return `<details class="privateTrace" ${state.openPrivate.has(s.span) ? "open" : ""} ontoggle="this.open ? state.openPrivate.add(${s.span}) : state.openPrivate.delete(${s.span})">
    <summary>${s.layer === "thought" ? "Private reasoning" : "Private memory"}${s.player ? ` · ${escapeHtml(tagOf(s.player))}` : ""}</summary>${content}</details>`;
}

function annoCard(t) {
  if (t.live) return liveAnnoCard(t);
  const [vc, vt] = valenceStyle(t.behavior);
  const ac = annotatorColor(t.annotator);
  const evidence = (t.evidence || []).map(e => evidenceQuote(t, e)).join("");
  return `<div class="annocard" style="background:${vt};border-color:${t.verified ? "var(--coop)" : "transparent"};border-left:3px solid ${vc}">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap">
      <span class="swatch" style="background:${AXIS_COLOR[axisOf(t.behavior)]}"></span>
      <span class="mono" style="font-size:10.5px;font-weight:600;color:${vc}">${t.behavior}</span>
      <span class="mono" style="font-size:9.5px;color:var(--mute-2)">${tagOf(t.player)} · ${t.confidence}/3</span>
      <span class="mono" style="font-size:9px;padding:2px 7px;border-radius:999px;color:${ac};background:color-mix(in srgb, ${ac} 12%, #fff)">${annotatorLabel(t.annotator)}</span>
      <span style="margin-left:auto">${verifyControl(t)}</span>
    </div>
    <div style="font-size:12px;line-height:1.45;color:var(--body)">${escapeHtml(t.note)}</div>
    ${evidence ? `<div style="margin-top:7px"><span class="mono" style="font-size:9px;color:var(--faint)">evidence</span>${evidence}</div>` : ""}
  </div>`;
}

/* the human's verdict on a panel tag: one toggle that marks it checked-and-correct. In a static
   export there is nowhere to save, so only an already-earned mark is shown. */
function verifyControl(t) {
  if (!VERIFICATIONS_URL)
    return t.verified ? '<span class="mono" style="font-size:9.5px;color:var(--coop);font-weight:600">✓ verified</span>' : "";
  return `<button class="verifybtn ${t.verified ? "on" : ""}"
    onclick="setTagVerified(${t.annotator}, '${t.behavior}', '${t.player}', ${t.turn}, ${!t.verified})"
    title="${t.verified ? "click to remove your verification" : "mark this tag as human-verified"}">${t.verified ? "✓ verified" : "✓ verify"}</button>`;
}

/* Each citation is printed in place — the cited line's kind, speaker, and text — so a tag reads
   without leaving the feed. Long lines collapse to the cited quote (or a leading snippet) behind
   a toggle; the [n] chip still opens the raw-trace drawer for full surrounding context. */
const EVIDENCE_COLLAPSE_LIMIT = 280;
function evidenceQuote(t, e) {
  const s = spanById.get(e.span);
  const chip = `<button class="chip" onclick="openLineDrawer(${e.span})" title="open in the raw trace">[${e.span}]</button>`;
  if (!s) return `<div class="evquote">${chip} <span class="mono" style="font-size:9.5px;color:var(--faint)">not in the trace (unresolved citation)</span></div>`;
  const [kindLabel, kindColor] = KINDS[s.layer] || KINDS.event;
  const key = `${t.annotator}.${t.behavior}.${t.player}.${t.turn}:${e.span}`;
  const long = s.text.length > EVIDENCE_COLLAPSE_LIMIT;
  const open = state.openEvidence.has(key);
  return `<div class="evquote" style="border-left-color:${kindColor}">
    <div class="evhead">
      ${chip}
      <span class="kindlabel">${kindLabel}</span>
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
  renderTimeline();
}

/* a live commentator's tag in the bubble stack: no confidence, no citations, no edit — just the
   member's read of the line, attributed with the 🎙 chip in the member's colour */
function liveAnnoCard(t) {
  const [vc, vt] = valenceStyle(t.behavior);
  const mc = MEMBER_COLORS[t.commentator_index % MEMBER_COLORS.length];
  return `<div class="annocard" style="background:${vt};border-left:3px solid ${vc}">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap">
      <span class="swatch" style="background:${AXIS_COLOR[axisOf(t.behavior)]}"></span>
      <span class="mono" style="font-size:10.5px;font-weight:600;color:${vc}">${t.behavior}</span>
      <span class="mono" style="font-size:9.5px;color:var(--mute-2)">${tagOf(t.player)}</span>
      <span class="mono" style="font-size:9px;padding:2px 7px;border-radius:999px;color:${mc};background:color-mix(in srgb, ${mc} 12%, #fff)">🎙 ${escapeHtml(t.commentator || "live")}</span>
    </div>
    <div style="font-size:12px;line-height:1.45;color:var(--body)">${escapeHtml(t.note || "")}</div>
  </div>`;
}

function commentaryBlock(comments) {
  return comments.map(c => {
    const label = GAME.commentators.length > 1 ? `<span class="mono" style="font-size:9px;color:${MEMBER_COLORS[c.commentator_index % MEMBER_COLORS.length]}">${escapeHtml(c.commentator)}</span> ` : "";
    // the commentator's own per-turn tags ride with its prose, so the live-commentary toggle governs both
    const pills = (c.tags || []).filter(t => state.filters.axes.has(axisOf(t.behavior))).map(commentatorPill).join("");
    return `<div class="commentary"><div style="display:flex;gap:9px;align-items:flex-start">
      <span class="live"><span class="swatch" style="background:var(--blue)"></span>live</span>
      <span style="flex:1"><span class="ctext">${richText(c.text)}</span></span></div>
      ${pills ? `<div class="tpills" style="padding:8px 0 0">${pills}</div>` : ""}
      <div style="margin-top:6px;font-family:var(--font-mono);font-size:9px;color:var(--faint)">${label}</div></div>`;
  }).join("");
}

/* a commentator tag has no confidence or evidence citation, so its pill is plain: valence tint,
   axis swatch, a 🎙 mark, and the justification in the tooltip */
function commentatorPill(t) {
  const [vc, vt] = valenceStyle(t.behavior);
  return `<span class="pill" title="${escapeHtml(t.note || "")}" style="background:${vt};color:${vc};cursor:${t.note ? "help" : "default"}">
    <span class="swatch" style="background:${AXIS_COLOR[axisOf(t.behavior)]}"></span>${t.behavior}
    <span class="mono" style="font-size:9px;color:${colorOf(t.player)};font-weight:600">${tagOf(t.player)}</span>
    <span style="font-size:9px;opacity:.6">🎙</span>
  </span>`;
}

function updateSubBar() {
  el("turnCount").textContent = turns.length + " turns";
  const tags = allTags().filter(tagVisible);
  const verified = tags.filter(t => t.verified).length;
  el("subStat").textContent = EVAL.has_evaluation
    ? `${tags.length} tags · ${verified} verified`
    : "not yet evaluated — run the panel to generate annotations";
}

function toggleExpand(turn) { state.expanded.has(turn) ? state.expanded.delete(turn) : state.expanded.add(turn); focusTurn(turn); renderTimeline(); }
function toggleAnno(turn, span) { const k = turn + ":" + span; state.openAnno.has(k) ? state.openAnno.delete(k) : state.openAnno.add(k); renderTimeline(); }
el("expandAll").onclick = () => { turns.forEach(t => state.expanded.add(t)); renderTimeline(); };
el("collapseAll").onclick = () => { state.expanded.clear(); renderTimeline(); };

/* ================= COMPARE (agreement matrix) ================= */
/* the matrix only offers the 1:1 code grains: family/axis cells stack several distinct codes per
   turn, so matching there would over-credit agreement — those grains live on the stats sheet's
   frequency views instead. Subfamily merges near-synonym codes into one row, so two members who
   read the same event under different synonyms line up as a co-detection. */
let compareGrain = "behavior";
function renderCompare() {
  const container = el("timeline");
  const active = annotators().filter(a => state.filters.annotators.has(a.index));
  const groups = new Map();
  allTags().filter(tagVisible).forEach(t => {
    const code = projectCode(t.behavior, compareGrain);
    const key = t.turn + "|" + t.player + "|" + code;
    if (!groups.has(key)) groups.set(key, { turn: t.turn, player: t.player, behavior: code, byAnn: {} });
    const group = groups.get(key);
    // merged synonym tags from one annotator collapse into the cell; show the strongest read
    group.byAnn[t.annotator] = Math.max(group.byAnn[t.annotator] ?? 0, t.confidence);
  });
  const rows = [...groups.values()].sort((a, b) => a.turn - b.turn || a.player.localeCompare(b.player));
  const head = `<div class="mhead"><span style="flex:1" class="eyebrow">behaviour · player · turn</span>${active.map(a =>
    `<span class="mcell mono" style="font-size:9px;color:${annotatorColor(a.index)}">${escapeHtml(a.label).slice(0, 4)}</span>`).join("")}</div>`;
  const body = rows.map(r => {
    const n = active.filter(a => r.byAnn[a.index] != null).length;
    const consensus = n >= 2;
    const [vc] = valenceStyleAtGrain(r.behavior, compareGrain);
    return `<div class="mrow" style="background:${consensus ? "#fafcfa" : "#fff"}">
      <span style="flex:1;display:flex;align-items:center;gap:7px;min-width:0">
        <span class="swatch" style="background:${AXIS_COLOR[axisAtGrain(r.behavior, compareGrain)] ?? "var(--neutral)"}"></span>
        <span class="mono" style="font-size:10.5px;color:${vc};font-weight:500">${r.behavior}</span>
        <span class="mono" style="font-size:9px;color:var(--mute-2)">${tagOf(r.player)} · t${r.turn}</span>
        <span class="mono" style="font-size:8.5px;padding:1px 6px;border-radius:999px;background:${consensus ? "#e2f0e9" : "#f6eede"};color:${consensus ? "var(--coop)" : "var(--gold)"}">${consensus ? "consensus" : "lone"}</span>
      </span>
      ${active.map(a => { const c = r.byAnn[a.index];
        return `<span class="mcell">${c != null ? `<span class="mmark" style="background:${annotatorColor(a.index)}">${c}</span>` : `<span class="mono" style="color:#d2ccbe">·</span>`}</span>`; }).join("")}
    </div>`;
  }).join("");
  const grainToggle = `<div class="grainControls" style="margin:0 0 8px"><span>group by:</span>` +
    ["behavior", "subfamily"].map(grain =>
      `<button data-grain="${grain}" class="${compareGrain === grain ? "on" : ""}">${grain}</button>`).join("") + `</div>`;
  container.innerHTML = grainToggle +
    `<div class="matrix">${head}${body || '<div class="empty" style="padding:14px">No tags match the current filters.</div>'}</div>`;
  container.querySelectorAll("button[data-grain]").forEach(button =>
    button.onclick = () => { compareGrain = button.dataset.grain; renderCompare(); });
  updateSubBar();
}
