/* ================= EVALUATE (configure + run the panel) ================= */
let modelCatalog = null;
async function loadCatalog() { if (modelCatalog) return modelCatalog; modelCatalog = await (await fetch("/api/models")).json(); return modelCatalog; }
function renderEvalCta() {
  const cta = el("evalCta");
  // once evaluated, the re-run affordance lives in the version bar; clear the CTA so its config
  // input ids never coexist with the bar's. no evaluate endpoint = static export = nothing to run.
  if (!EVALUATE_URL || (EVAL.has_evaluation && !EVAL.running)) { cta.hidden = true; cta.innerHTML = ""; return; }
  cta.hidden = false;
  if (EVAL.running) {
    cta.innerHTML = `<div style="display:flex;align-items:center;gap:12px"><span class="mono" style="font-size:13px;color:var(--mute)">${evalProgressHtml()}</span></div>`;
    return;
  }
  cta.innerHTML = `<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">
      <div style="flex:1;min-width:240px"><h2>This game is over — not yet evaluated</h2>
        <p>Run the evaluator panel to read all ${turns.length} turns and generate behaviour annotations, deception-gap detection, and a per-player synthesis. Each tag can then be human-verified in the timeline.</p>
        ${interruptedNoteHtml()}</div>
      <button class="btn dark" id="evalToggle" style="font-size:12.5px;padding:11px 20px">▶ Configure panel</button></div>
    <div class="evalConfig" id="evalConfig"><div id="evalRows"></div>
      <div style="display:flex;gap:8px;margin-top:8px"><button class="btn" id="evalAdd">+ add member</button>
      <button class="btn dark" id="evalRun">▶ Run evaluation</button></div></div>`;
  el("evalToggle").onclick = async () => { await loadCatalog(); el("evalConfig").classList.toggle("open"); if (!el("evalRows").children.length) addEvalRow(); };
  el("evalAdd") && (el("evalAdd").onclick = () => addEvalRow());
  el("evalRun") && (el("evalRun").onclick = runEvaluation);
}

/* the version bar, shown once a game has at least one evaluation: switch between runs and re-run. */
function renderEvalBar() {
  const bar = el("evalBar");
  const versions = EVAL.versions || [];
  if (!EVAL.has_evaluation || !versions.length) { bar.hidden = true; bar.innerHTML = ""; return; }
  bar.hidden = false;
  const current = versions.find(v => v.is_current) || versions[0];
  const viewing = selectedVersion || current.run_id;  // "" follows the current run
  const options = versions.map(v =>
    `<option value="${v.is_current ? "" : v.run_id}"${v.run_id === viewing ? " selected" : ""}>${escapeHtml(versionLabel(v))}</option>`).join("");
  const runControl = EVAL.running
    ? `<span class="mono versionMeta">${evalProgressHtml()}</span>`
    : `${interruptedNoteHtml()}${EVALUATE_URL ? `<button class="btn" id="rerunBtn">▶ Re-run panel</button>` : ""}`;
  bar.innerHTML = `<div class="evalBarRow">
      <span class="eyebrow">Evaluation</span>
      <select class="versionSelect" id="versionSelect">${options}</select>
      <span class="versionMeta">${versions.length} version${versions.length > 1 ? "s" : ""}</span>
      <span style="flex:1"></span>${runControl}</div>
    <div class="evalConfig" id="evalConfig"><div id="evalRows"></div>
      <div style="display:flex;gap:8px;margin-top:8px"><button class="btn" id="evalAdd">+ add member</button>
      <button class="btn dark" id="evalRun">▶ Run evaluation</button></div></div>`;
  el("versionSelect").onchange = e => { selectedVersion = e.target.value; switchVersion(); };
  const rerun = el("rerunBtn");
  if (rerun) rerun.onclick = async () => { await loadCatalog(); const cfg = el("evalConfig"); cfg.classList.toggle("open");
    if (cfg.classList.contains("open") && !el("evalRows").children.length) prefillPanel(current); };
  el("evalAdd") && (el("evalAdd").onclick = () => addEvalRow());
  el("evalRun") && (el("evalRun").onclick = runEvaluation);
}
/* the live progress line while a panel runs: calls done / expected and the running cost, from
   the growing eval trace the server aggregates into EVAL.progress on every poll */
function evalProgressHtml() {
  const p = EVAL.progress;
  if (!p || p.state !== "running") return `◐ evaluating… the panel is reading all ${turns.length} turns`;
  const cost = p.cost_usd != null ? ` · ~$${p.cost_usd.toFixed(2)}` : "";
  const failed = p.failed_calls ? ` · <span style="color:var(--adversarial)">${p.failed_calls} failed</span>` : "";
  const members = (p.members || []).map(m => `${m.spec.split(":")[0]} ${m.calls}/${m.expected_calls}`).join(", ");
  return `◐ evaluating · ${p.completed_calls}/${p.expected_calls} calls${cost}${failed}`
    + (members ? ` <span style="color:var(--mute-2)">(${members})</span>` : "");
}

/* a run that died before writing its sidecar (e.g. the server went down mid-run) left only an
   orphaned trace; surface the silent death and what it spent instead of pretending nothing ran */
function interruptedNoteHtml() {
  const p = EVAL.progress;
  if (EVAL.running || !p || p.state !== "interrupted") return "";
  const cost = p.cost_usd != null ? ` (~$${p.cost_usd.toFixed(2)} spent)` : "";
  return `<span class="mono versionMeta" style="color:var(--adversarial)">⚠ the last run was interrupted after ${p.completed_calls}/${p.expected_calls} calls${cost}</span>`;
}

function versionLabel(v) {
  const panel = ((v.labels && v.labels.length ? v.labels : v.panel) || []).join(" │ ") || "evaluation";
  return `${panel} · ${shortDate(v.created_at)}${v.is_current ? " · current" : ""}`;
}
function shortDate(iso) {
  const d = new Date(iso);
  return isNaN(d) ? (iso || "") : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
async function switchVersion() {
  await refreshEvaluation(); rebuildTrace();
  renderRail(); renderTimeline(); renderSynthesis(); renderEvalBar();
  if (typeof statsOpen === "function" && statsOpen()) renderStats();
}
/* seed the config with the last panel so re-running defaults to the same members */
function prefillPanel(version) {
  el("evalRows").innerHTML = "";
  (version.panel || []).forEach(spec => addEvalRow(spec));
  if (!el("evalRows").children.length) addEvalRow();
}
function addEvalRow(spec) {
  const providers = Object.keys(modelCatalog.tiers_by_provider);
  const parts = (spec || "").split(":");  // provider:tier:effort
  const row = document.createElement("div"); row.className = "evalRow";
  const opt = (arr, sel) => arr.map(v => `<option${v === sel ? " selected" : ""}>${v}</option>`).join("");
  row.innerHTML = `<select class="evP">${opt(providers, parts[0])}</select><select class="evT"></select><select class="evE">${opt(modelCatalog.efforts, parts[2])}</select>
    <button class="btn" onclick="this.parentNode.remove()">✕</button>`;
  el("evalRows").appendChild(row);
  const syncTiers = sel => { const tiers = Object.keys(modelCatalog.tiers_by_provider[row.querySelector(".evP").value]);
    row.querySelector(".evT").innerHTML = opt(tiers, sel); };
  row.querySelector(".evP").onchange = () => syncTiers(); syncTiers(parts[1]);
}
async function runEvaluation() {
  const rows = [...el("evalRows").children].map(r =>
    `${r.querySelector(".evP").value}:${r.querySelector(".evT").value}:${r.querySelector(".evE").value}`);
  if (!rows.length) { toast("add at least one panel member"); return; }
  const res = await fetch(EVALUATE_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ evaluators: rows }) });
  if (!res.ok) { toast("could not start: " + (await res.json()).detail); return; }
  selectedVersion = "";  // follow the newest run so this fresh evaluation becomes what we view
  EVAL.running = true; renderEvalCta(); renderEvalBar();
  pollEvaluation();
}

/* poll while a run is in flight (also picked up on page load when a run is already going) */
let evalPollTimer = null;
function pollEvaluation() {
  if (evalPollTimer) return;  // one poller is enough
  evalPollTimer = setInterval(async () => { await refreshEvaluation();
    if (!EVAL.running) { clearInterval(evalPollTimer); evalPollTimer = null;
      rebuildTrace(); renderRail(); renderTimeline(); renderSynthesis(); toast("evaluation complete");
      if (statsOpen()) renderStats(); }
    renderEvalCta(); renderEvalBar(); }, 2500);
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

