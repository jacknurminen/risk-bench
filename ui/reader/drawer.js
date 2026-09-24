/* Research detail and exact trace citations share one drawer. */
function openLineDrawer(span) {
  state.drawer = {focus: span, cites: new Set([span])}; renderPanels();
}
function openInterval(start, end) {
  state.drawer = {focus: start, interval: [start,end], cites: new Set()}; renderPanels();
}
function openUnitDrawer(key) {
  const unit = allUnits().find(u => unitKey(u) === key);
  if (!unit) { toast("This unit is not available in the selected run."); return; }
  state.drawer = {unit, focus: null, cites: new Set(unitCitations(unit).map(e => e.span))}; renderPanels();
}
function chipClick(span) { openLineDrawer(span); }
function closeDrawer() { state.drawer = null; renderPanels(); }
async function setUnitVerified(key, verified) {
  const unit = allUnits().find(u => unitKey(u) === key);
  if (!unit) return;
  const res = await fetch(VERIFICATIONS_URL, {method:"PUT", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({evaluator_index:unit.annotator, unit_type:unit.kind, unit_id:unit.id,
      verified, version:EVAL.version})});
  if (!res.ok) { toast("Could not save the review."); return; }
  await refreshEvaluation();
  renderTimeline();
  if (state.drawer?.unit) openUnitDrawer(key);
  toast(verified ? "Review saved" : "Review removed");
}
function renderPanels() {
  const root = el("panelRoot"), d = state.drawer;
  if (!d) { root.innerHTML = ""; return; }
  const source = (EVAL.spans || []).filter(s => !d.interval || (s.span >= d.interval[0] && s.span <= d.interval[1]));
  root.innerHTML = '<div class="overlay" onclick="closeDrawer()"></div><aside class="drawer">' +
    '<div class="dhead"><span>' + (d.unit ? 'Evidence & raw trace' : 'Raw trace') + ' · ' + escapeHtml(EVAL.game_id || "") +
    '</span><button class="btn" onclick="closeDrawer()">Close</button></div><div class="dbody">' +
    (d.unit ? unitCard(d.unit, true) : "") +
    (d.interval ? '<p>Complete recorded interval [' + d.interval.join("–") + ']. Review its endpoint against the commitment terms.</p>' : "") +
    source.map(s => '<div class="traceRow" id="drawer-span-' + s.span + '"' +
      (d.cites.has(s.span) ? ' style="background:var(--neutral-tint)"' : "") +
      '><button class="chip" onclick="show(' + s.step + ')">[' + s.span + ']</button><span>' +
      '<span class="kindlabel">' + escapeHtml(gameTimeLabel(s.turn,s.round) + " · " + s.layer +
      " · record " + s.record + (s.decision_id ? " · " + s.decision_id : "")) +
      '</span><span class="tracetext mono">' + richText(s.text) + '</span>' +
      (s.data ? '<details><summary>Recorded action / state</summary><pre>' + escapeHtml(JSON.stringify(s.data,null,2)) + '</pre></details>' : "") +
      '</span></div>').join("") + '</div></aside>';
  if (d.focus != null) requestAnimationFrame(() => el("drawer-span-" + d.focus)?.scrollIntoView({block:"center"}));
}
