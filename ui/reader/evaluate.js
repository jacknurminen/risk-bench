/* ================= EVALUATE (configure + run the panel) ================= */
let modelCatalog = null;
async function loadCatalog() { if (modelCatalog) return modelCatalog; modelCatalog = await (await fetch("/api/models")).json(); return modelCatalog; }
function renderEvalCta() {
  const cta = el("evalCta");
  // no evaluate endpoint means a static export: nothing to run, so no call to action
  if (!EVALUATE_URL || (EVAL.has_evaluation && !EVAL.running)) { cta.hidden = true; return; }
  cta.hidden = false;
  if (EVAL.running) {
    cta.innerHTML = `<div style="display:flex;align-items:center;gap:12px"><span class="mono" style="font-size:13px;color:var(--mute)">◐ evaluating… the panel is reading all ${turns.length} turns</span></div>`;
    return;
  }
  cta.innerHTML = `<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">
      <div style="flex:1;min-width:240px"><h2>This game is over — not yet evaluated</h2>
        <p>Run the evaluator panel to read all ${turns.length} turns and generate behaviour annotations, deception-gap detection, and a per-player synthesis. Each tag can then be human-verified in the timeline.</p></div>
      <button class="btn dark" id="evalToggle" style="font-size:12.5px;padding:11px 20px">▶ Configure panel</button></div>
    <div class="evalConfig" id="evalConfig"><div id="evalRows"></div>
      <div style="display:flex;gap:8px;margin-top:8px"><button class="btn" id="evalAdd">+ add member</button>
      <button class="btn dark" id="evalRun">▶ Run evaluation</button></div></div>`;
  el("evalToggle").onclick = async () => { await loadCatalog(); el("evalConfig").classList.toggle("open"); if (!el("evalRows").children.length) addEvalRow(); };
  el("evalAdd") && (el("evalAdd").onclick = addEvalRow);
  el("evalRun") && (el("evalRun").onclick = runEvaluation);
}
function addEvalRow() {
  const providers = Object.keys(modelCatalog.tiers_by_provider);
  const row = document.createElement("div"); row.className = "evalRow";
  const opt = (arr, sel) => arr.map(v => `<option${v === sel ? " selected" : ""}>${v}</option>`).join("");
  row.innerHTML = `<select class="evP">${opt(providers)}</select><select class="evT"></select><select class="evE">${opt(modelCatalog.efforts)}</select>
    <button class="btn" onclick="this.parentNode.remove()">✕</button>`;
  el("evalRows").appendChild(row);
  const syncTiers = () => { const tiers = Object.keys(modelCatalog.tiers_by_provider[row.querySelector(".evP").value]);
    row.querySelector(".evT").innerHTML = opt(tiers); };
  row.querySelector(".evP").onchange = syncTiers; syncTiers();
}
async function runEvaluation() {
  const rows = [...el("evalRows").children].map(r =>
    `${r.querySelector(".evP").value}:${r.querySelector(".evT").value}:${r.querySelector(".evE").value}`);
  if (!rows.length) { toast("add at least one panel member"); return; }
  const res = await fetch(EVALUATE_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ evaluators: rows }) });
  if (!res.ok) { toast("could not start: " + (await res.json()).detail); return; }
  EVAL.running = true; renderEvalCta();
  const poll = setInterval(async () => { await refreshEvaluation();
    if (!EVAL.running) { clearInterval(poll); renderRail(); renderTimeline(); renderSynthesis(); toast("evaluation complete"); }
    renderEvalCta(); }, 2500);
}

/* ================= SYNTHESIS ================= */
function renderSynthesis() {
  const section = el("synthSection");
  const synth = {};
  (EVAL.members || []).forEach(m => (m.syntheses || []).forEach(s => { synth[s.player] ??= s; }));
  if (!EVAL.has_evaluation || !Object.keys(synth).length) { section.hidden = true; return; }
  section.hidden = false;
  section.innerHTML = `<div class="eyebrow" style="margin-bottom:12px">Panel synthesis · whole-game</div>
    <div class="synthGrid">${GAME.players.map(p => { const s = synth[p.name]; if (!s) return "";
      const [rl, rc] = playerResult(p.name);
      return `<article class="synthCard" style="border-top-color:${colorOf(p.name)}">
        <div style="display:flex;gap:10px;align-items:center"><span class="tbadge" style="background:${colorOf(p.name)}">${initialsOf(p.name)}</span>
          <span class="ptag" style="color:${colorOf(p.name)};flex:1">${tagOf(p.name)}</span>
          <span class="presult" style="color:${rc};background:color-mix(in srgb, ${rc} 13%, #fff)">${rl}</span></div>
        <p class="summary">${richText(s.summary)}</p>
        ${(s.profile || []).map(d => `<div class="synthDim"><b>${d.dimension}:</b> ${richText(d.characterization)}</div>`).join("")}
      </article>`; }).join("")}</div>`;
}

