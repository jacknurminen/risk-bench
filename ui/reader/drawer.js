/* ================= EVIDENCE DRAWER ================= */
function openLineDrawer(span) { state.drawer = { focus: span, cites: new Set([span]) }; renderPanels(); }
function openTagDrawer(annotatorIndex, behavior, turn) {
  const tags = allTags().filter(t => t.turn === turn && t.behavior === behavior && t.annotator === annotatorIndex);
  const t = tags[0]; if (!t) return;
  const cites = (t.evidence || []).map(e => e.span);
  state.drawer = { focus: cites[0] ?? null, cites: new Set(cites), tag: t }; renderPanels();
}
function chipClick(span) { openLineDrawer(span); }
function closeDrawer() { state.drawer = null; renderPanels(); }

/* ================= TAG VERIFICATION ================= */
/* Humans no longer author their own tags; they verify the panel's. One PUT sets or clears the
   human-verified mark on a tag, keyed the same way the agreement aggregate groups tags. */
async function setTagVerified(annotatorIndex, behavior, player, turn, verified) {
  // verifications are per version, so tie the verdict to the run currently being viewed
  const body = { evaluator_index: annotatorIndex, player, behavior, turn, verified, version: viewedVersionRunId() };
  const res = await fetch(VERIFICATIONS_URL, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) { toast("could not save the verification"); return; }
  await refreshEvaluation();
  renderTimeline();
  toast(verified ? "tag verified" : "verification removed");
}

/* ================= RIGHT-SIDE PANEL RENDER ================= */
function renderPanels() {
  const root = el("panelRoot");
  if (state.drawer) { root.innerHTML = drawerPanel(); return; }
  root.innerHTML = "";
}

function drawerPanel() {
  const d = state.drawer;
  const rows = (EVAL.spans || []).map(s => {
    const hot = d.cites.has(s.span), focus = s.span === d.focus;
    const pc = s.player ? colorOf(s.player) : "#9aa3ae";
    return `<div class="traceRow" style="background:${hot ? "color-mix(in srgb, " + pc + " 9%, #fff)" : "transparent"};${focus ? "box-shadow:inset 0 0 0 1px " + pc : ""}">
      <span class="ln">[${s.span}]</span><span class="swatch" style="background:${pc};margin-top:5px"></span>
      <span class="mono" style="font-size:11px;line-height:1.5;color:${hot ? "var(--ink)" : "var(--mute-2)"};flex:1">${richText(s.text)}</span></div>`;
  }).join("");
  const title = d.tag ? `${d.tag.behavior} · ${tagOf(d.tag.player)}` : `trace line [${d.focus}]`;
  return `<div class="overlay" onclick="closeDrawer()"></div><aside class="drawer">
    <div class="dhead"><div><span class="eyebrow">Evidence · raw trace</span>
      <div class="mono" style="font-size:12px;font-weight:600;margin-top:6px">${escapeHtml(title)}</div>
      ${d.tag ? `<div style="font-size:12.5px;color:var(--body);margin-top:4px">${escapeHtml(d.tag.note)}</div>` : ""}</div>
      <button class="iconbtn" onclick="closeDrawer()" style="font-size:18px;color:var(--mute-2)">✕</button></div>
    <div class="dbody">${rows}</div></aside>`;
}
