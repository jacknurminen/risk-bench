/* Research details and raw trace have independent drawers. */
function openLineDrawer(span) {
  state.drawer = {focus: span, cites: new Set([span])}; renderPanels();
}
function openInterval(start, end) {
  state.drawer = {focus: start, interval: [start,end], cites: new Set()}; renderPanels();
}
function openUnitDrawer(key) {
  const unit = isTwoTagSchema() ?
    orderedV3Units().find(u => unitKey(u) === key || u.members?.some(m => unitKey(m) === key)) :
    allUnits().find(u => unitKey(u) === key);
  if (!unit) { toast("This unit is not available in the selected run."); return; }
  const selectedKey = unitKey(unit);
  if (isTwoTagSchema() && state.unitDrawer?.key === selectedKey) {
    state.unitDrawer = {key:null}; state.openTagEvidence = false; renderPanels(); return;
  }
  state.openTagEvidence = false;
  state.unitDrawer = {key:selectedKey, undertakings:new Set(
    (unit.members || []).filter(m => unitKey(m) === key).map(m => m.id))}; renderPanels();
  if (isTwoTagSchema()) requestAnimationFrame(() =>
    el("tag-event-" + v3Number(unit))?.scrollIntoView({block:"center"}));
}
function chipClick(span) { openLineDrawer(span); }
function closeDrawer() { state.drawer = null; renderPanels(); }
function closeUnitDrawer() { state.unitDrawer = null; state.openTagEvidence = false; renderPanels(); }
function closePanels() { state.drawer = null; state.unitDrawer = null; state.openTagEvidence = false; renderPanels(); }
async function setUnitVerified(key, verified) {
  const unit = isTwoTagSchema() ?
    orderedV3Units().find(u => unitKey(u) === key) : allUnits().find(u => unitKey(u) === key);
  if (!unit) return;
  for (const member of unit.members || [unit]) {
    const res = await fetch(VERIFICATIONS_URL, {method:"PUT", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({evaluator_index:member.annotator, unit_type:member.kind, unit_id:member.id,
        verified, version:EVAL.version})});
    if (!res.ok) { toast("Could not save the review."); return; }
  }
  await refreshEvaluation();
  renderTimeline();
  if (state.unitDrawer) renderPanels();
  toast(verified ? "Review saved" : "Review removed");
}
function renderPanels() {
  const root = el("panelRoot"), d = state.drawer;
  const unitScroll = root.querySelector(".unitDrawer .dbody")?.scrollTop;
  const unitTimeline = isTwoTagSchema() && !!state.unitDrawer;
  const unit = state.unitDrawer?.key && (unitTimeline ? orderedV3Units() : allUnits())
    .find(u => unitKey(u) === state.unitDrawer.key);
  if (state.unitDrawer?.key && !unit) state.unitDrawer = unitTimeline ? {key:null} : null;
  if (!unitTimeline && !unit) state.unitDrawer = null;
  const showUnit = unitTimeline || !!unit;
  if (!d && !showUnit) { root.innerHTML = ""; return; }
  const source = d ? (EVAL.spans || []).filter(s => !d.interval || (s.span >= d.interval[0] && s.span <= d.interval[1])) : [];
  root.innerHTML = '<div class="overlay" onclick="closePanels()"></div>' +
    (showUnit ? '<aside class="drawer unitDrawer' + (unit ? ' researchTone-' + unitTone(unit) : '') + '" aria-label="' +
      escapeHtml(isThesisSchema() ? "Deception and commitment timeline" : isTwoTagSchema() ? "Statement and commitment timeline" : unitType(unit) + " details") +
      '"><div class="dhead"><span>' + (isThesisSchema() ? 'Deception &amp; commitments' : isTwoTagSchema() ? 'Statements &amp; commitments' :
      escapeHtml(unitType(unit) + ' · ' + (EVAL.game_id || ""))) +
      '</span><button class="btn" onclick="closeUnitDrawer()">Close</button></div>' +
      '<div class="dbody">' + (isTwoTagSchema() ?
        '<div class="tagTimeline">' + orderedV3Units().map((v,index) =>
          unitCardV3(v,unitKey(v) === state.unitDrawer.key,index+1)).join('') + '</div>' :
        unitCard(unit, true)) +
      '</div></aside>' : '') +
    (d ? '<aside class="drawer rawDrawer' + (showUnit ? ' withUnit' : '') + '" aria-label="Raw trace"><div class="dhead"><span>Raw trace · ' +
      escapeHtml(EVAL.game_id || "") + '</span><button class="btn" onclick="closeDrawer()">Close</button></div><div class="dbody">' +
      (d.interval ? '<p>Recorded interval [' + d.interval.join("–") + ']. Review its endpoint against the terms.</p>' : "") +
      source.map(s => '<div class="traceRow" id="drawer-span-' + s.span + '" style="border-left:2px solid ' +
        (KINDS[s.layer]?.[1] || 'var(--line)') + ';' + (d.cites.has(s.span) ? 'background:var(--neutral-tint)' : '') + '">' +
        '<button class="chip" onclick="show(' + s.step + ')">[' + s.span + ']</button><span>' +
        '<span class="kindlabel">' + escapeHtml(gameTimeLabel(s.turn,s.round) + ' · ' + s.layer +
        ' · record ' + s.record + (s.decision_id ? ' · ' + s.decision_id : '')) +
        (s.player ? ' · <b style="color:' + colorOf(s.player) + '">' + escapeHtml(s.player) + '</b>' : '') +
        '</span><span class="tracetext mono">' + richText(s.text) + '</span>' +
        '</span></div>').join('') + '</div></aside>' : '');
  const unitBody = root.querySelector(".unitDrawer .dbody");
  if (unitBody && unitScroll != null) unitBody.scrollTop = unitScroll;
  if (d?.focus != null) requestAnimationFrame(() => el("drawer-span-" + d.focus)?.scrollIntoView({block:"center"}));
}
