/* ================= EVIDENCE DRAWER ================= */
function openLineDrawer(span) { state.drawer = { focus: span, cites: new Set([span]) }; renderPanels(); }
function openTagDrawer(idOrIndex, behavior, turn) {
  const tags = allTags().filter(t => t.turn === turn && t.behavior === behavior &&
    (typeof idOrIndex === "string" ? t.id === idOrIndex : t.annotator === idOrIndex));
  const t = tags[0]; if (!t) return;
  const cites = (t.evidence || []).map(e => e.span);
  state.drawer = { focus: cites[0] ?? null, cites: new Set(cites), tag: t }; renderPanels();
}
function chipClick(span) { if (state.editor) toggleCite(span); else openLineDrawer(span); }
function closeDrawer() { state.drawer = null; renderPanels(); }

/* ================= ANNOTATION EDITOR ================= */
function openEditor(turn, span) {
  const actor = turnActor(turn) || GAME.players[0].name;
  state.editor = { id: null, turn, player: actor, behavior: null, confidence: 2, note: "", citations: new Set(span != null ? [span] : []) };
  state.drawer = null; renderPanels(); renderTimeline();
}
el("newAnno").onclick = () => openEditor(state.focusTurn, null);
if (!ANNOTATIONS_URL) el("newAnno").hidden = true;  // static export: nowhere to save
function editAnnotation(id) {
  const t = (EVAL.annotations || []).find(a => a.id === id); if (!t) return;
  state.editor = { id, turn: t.turn, player: t.player, behavior: t.behavior, confidence: t.confidence, note: t.note,
    citations: new Set((t.evidence || []).map(e => e.span)) };
  renderPanels(); renderTimeline();
}
function closeEditor() { state.editor = null; renderPanels(); renderTimeline(); }
function setEd(patch) { Object.assign(state.editor, patch); renderPanels(); if ("citations" in patch) renderTimeline(); }
function toggleCite(span) { const s = state.editor.citations; s.has(span) ? s.delete(span) : s.add(span); renderPanels(); renderTimeline(); }

async function saveEditor() {
  const e = state.editor; if (!e || !e.behavior) { toast("pick a behaviour label first"); return; }
  const body = { player: e.player, behavior: e.behavior, confidence: e.confidence, note: el("edNote").value.trim(),
    evidence: [...e.citations].sort((a, b) => a - b).map(span => ({ span })) };
  if (!body.evidence.length) body.turn = e.turn;
  const url = e.id ? `${ANNOTATIONS_URL}/${e.id}` : ANNOTATIONS_URL;
  const res = await fetch(url, { method: e.id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) { toast("could not save: " + (await res.json()).detail); return; }
  state.editor = null; await refreshEvaluation(); renderPanels(); renderRail(); renderTimeline();
  toast("annotation saved");
}
async function deleteAnnotation(id) {
  const res = await fetch(`${ANNOTATIONS_URL}/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) { toast("could not delete"); return; }
  if (state.editor && state.editor.id === id) state.editor = null;
  await refreshEvaluation(); renderPanels(); renderRail(); renderTimeline(); toast("annotation deleted");
}

/* ================= RIGHT-SIDE PANEL RENDER ================= */
function renderPanels() {
  const root = el("panelRoot");
  if (state.editor) { root.innerHTML = editorPanel(); wireEditor(); return; }
  if (state.drawer) { root.innerHTML = drawerPanel(); return; }
  root.innerHTML = "";
}
function editorPanel() {
  const e = state.editor;
  const players = GAME.players.map(p => { const on = e.player === p.name;
    return `<button class="vocabItem" onclick="setEd({player:'${p.name}'})" style="flex:1;${on ? `border-color:${colorOf(p.name)};color:${colorOf(p.name)};font-weight:600` : ""}">${initialsOf(p.name)} ${tagOf(p.name)}</button>`; }).join("");
  const vocab = AXES.map(([axis, label, color]) => {
    const items = (EVAL.vocabulary || []).filter(v => v.axis === axis).map(v => { const on = e.behavior === v.behavior;
      const [vc, vt] = valenceStyle(v.behavior);
      return `<button class="vocabItem" onclick="setEd({behavior:'${v.behavior}'})" style="${on ? `border-color:${vc};background:${vt};color:${vc};font-weight:600` : ""}">${v.behavior}</button>`; }).join("");
    return `<div class="vocabGroup"><div style="display:flex;align-items:center;gap:6px;margin-bottom:5px"><span class="swatch" style="background:${color}"></span><span class="mono" style="font-size:9.5px;color:var(--mute-2)">${label}</span></div><div class="items">${items}</div></div>`;
  }).join("");
  const conf = [1, 2, 3].map(n => `<button class="${e.confidence === n ? "on" : ""}" onclick="setEd({confidence:${n}})">${n}/3</button>`).join("");
  const cites = [...e.citations].sort((a, b) => a - b).map(span => `<button class="chip" onclick="toggleCite(${span})">[${span}] ✕</button>`).join(" ");
  return `<aside class="drawer" style="width:430px">
    <div class="dhead"><span class="serif" style="font-size:16px;font-weight:600">${e.id ? "Edit annotation" : "New annotation"}</span>
      <button class="iconbtn" onclick="closeEditor()" style="font-size:18px;color:var(--mute-2)">✕</button></div>
    <div class="dbody">
      <div class="field"><div class="flabel">Player</div><div style="display:flex;gap:6px">${players}</div></div>
      <div class="field"><div class="flabel">Behaviour label</div>${vocab}</div>
      <div class="field"><div class="flabel">Confidence</div><div class="seg" style="margin-bottom:0">${conf}</div></div>
      <div class="field"><div class="flabel">Justification</div><textarea id="edNote" placeholder="One-line rationale for the tag…">${escapeHtml(e.note)}</textarea></div>
      <div class="field"><div class="flabel">Evidence · linked lines <span style="color:${e.citations.size ? "var(--coop)" : "var(--mute)"}">(${e.citations.size})</span></div>
        <div class="mono" style="font-size:10.5px;color:var(--mute-2);line-height:1.5;margin-bottom:9px">Click the <span class="chip">[n]</span> chips on any trace line to link or unlink evidence.</div>
        <div style="display:flex;flex-wrap:wrap;gap:5px;min-height:26px">${cites}</div></div>
    </div>
    <div class="dfoot"><button class="btn dark" onclick="saveEditor()">${e.id ? "update tag" : "save tag"}</button>
      <button class="btn" onclick="closeEditor()">cancel</button>
      ${e.id ? `<button class="mono" onclick="deleteAnnotation('${e.id}')" style="margin-left:auto;background:none;border:none;cursor:pointer;color:var(--adversarial);text-decoration:underline">delete</button>` : ""}</div>
  </aside>`;
}
function wireEditor() { const ta = el("edNote"); if (ta) ta.focus(); }

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

