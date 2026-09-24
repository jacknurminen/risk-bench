/* ================= EVALUATE (configure + run the panel) ================= */
let modelCatalog = null;
async function loadCatalog() { if (modelCatalog) return modelCatalog; modelCatalog = await (await fetch("/api/models")).json(); return modelCatalog; }
function renderEvalCta() {
  const cta = el("evalCta");
  // once evaluated, the re-run affordance lives in the version bar; clear the CTA so its config
  // input ids never coexist with the bar's. no evaluate endpoint = static export = nothing to run.
  if (!EVALUATE_URL || EVAL.has_evaluation) { cta.hidden = true; cta.innerHTML = ""; return; }
  cta.hidden = false;
  if (EVAL.running) {
    cta.innerHTML = `<div style="display:flex;align-items:center;gap:12px"><span class="mono" style="font-size:13px;color:var(--mute)">${evalProgressHtml()}</span></div>`;
    return;
  }
  cta.innerHTML = `<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">
      <div style="flex:1;min-width:240px"><h2>${GAME.result ? "Game finished" : "Live research is off"} — not yet evaluated</h2>
        <p>${GAME.result ? "Choose an evaluator to read the recorded game." : "Choose an evaluator to follow completed turns. It will also catch up on turns already played."} Commitments, claims and a short turn note appear when the model has finished each turn.</p>
        ${interruptedNoteHtml()}</div>
      <button class="btn dark" id="evalToggle" style="font-size:12.5px;padding:11px 20px">▶ Choose evaluator</button></div>
    <div class="evalConfig" id="evalConfig"><div id="evalRows"></div>
      <div style="display:flex;gap:8px;margin-top:8px;align-items:center"><button class="btn" id="evalAdd">+ add member</button>
      ${GAME.result && !(EVAL.live_evaluation && !EVAL.live_finalized) ? "" : '<label class="mono"><input type="checkbox" id="evalLive" checked> follow completed turns</label>'}
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
  const runLabel = EVAL.live_evaluation && GAME.result && !EVAL.live_finalized
    ? "Finish live review" : EVAL.live_evaluation && EVAL.error ? "Resume live analysis" : "Re-run panel";
  const runControl = EVAL.running
    ? `<span class="mono versionMeta">${evalProgressHtml()}</span>`
    : `${interruptedNoteHtml()}${EVALUATE_URL ? `<button class="btn" id="rerunBtn">▶ ${runLabel}</button>` : ""}`;
  const hiddenNote = EVAL.live_evaluation && !state.filters.showCommentary
    ? '<span class="versionMeta">Notes hidden · enable research annotations in the left rail</span>' : "";
  bar.innerHTML = `<div class="evalBarRow">
      <span class="eyebrow">Evaluation</span>
      <select class="versionSelect" id="versionSelect">${options}</select>
      <span class="versionMeta">${versions.length} version${versions.length > 1 ? "s" : ""}</span>
      <span style="flex:1"></span>${hiddenNote}${runControl}</div>
    <div class="evalConfig" id="evalConfig"><div id="evalRows"></div>
      <div style="display:flex;gap:8px;margin-top:8px;align-items:center"><button class="btn" id="evalAdd">+ add member</button>
      ${GAME.result && !(EVAL.live_evaluation && !EVAL.live_finalized) ? "" : '<label class="mono"><input type="checkbox" id="evalLive" checked> follow completed turns</label>'}
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
  if (EVAL.live_evaluation) return `◐ following completed turns · through turn ${EVAL.evaluated_through_turn ?? 0}${GAME.result && !EVAL.live_finalized ? " · final review pending" : ""}`;
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
  if (EVAL.error && !EVAL.running) return `<span class="mono versionMeta" style="color:var(--adversarial)">⚠ evaluation paused: ${escapeHtml(EVAL.error)}</span>`;
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
  const providers = Object.keys(modelCatalog.models_by_provider);
  const parts = (spec || "").split(":");  // provider:model:effort
  const row = document.createElement("div"); row.className = "evalRow";
  const opt = (arr, sel) => arr.map(v => `<option${v === sel ? " selected" : ""}>${v}</option>`).join("");
  const modelOpt = (models, sel) => models.map(m =>
    `<option value="${m.id}"${m.id === sel ? " selected" : ""}>${m.id} · $${m.input_usd_per_million}/$${m.output_usd_per_million}</option>`).join("");
  row.innerHTML = `<select class="evP">${opt(providers, parts[0])}</select><select class="evM"></select><select class="evE">${opt(modelCatalog.efforts, parts[2])}</select>
    <button class="btn" onclick="this.parentNode.remove()">✕</button>`;
  el("evalRows").appendChild(row);
  const syncModels = sel => { const models = modelCatalog.models_by_provider[row.querySelector(".evP").value] ?? [];
    row.querySelector(".evM").innerHTML = modelOpt(models, sel); };
  row.querySelector(".evP").onchange = () => syncModels(); syncModels(parts[1]);
}
async function runEvaluation() {
  const rows = [...el("evalRows").children].map(r =>
    `${r.querySelector(".evP").value}:${r.querySelector(".evM").value}:${r.querySelector(".evE").value}`);
  if (!rows.length) { toast("add at least one panel member"); return; }
  const live = !!el("evalLive")?.checked;
  const res = await fetch(EVALUATE_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ evaluators: rows, live }) });
  if (!res.ok) { toast("could not start: " + (await res.json()).detail); return; }
  selectedVersion = "";  // follow the newest run so this fresh evaluation becomes what we view
  EVAL.running = true; renderEvalCta(); renderEvalBar();
  pollEvaluation();
}

/* poll while a run is in flight (also picked up on page load when a run is already going) */
let evalPollTimer = null;
function pollEvaluation() {
  if (evalPollTimer) return;  // one poller is enough
  evalPollTimer = setInterval(async () => {
    const before = `${EVAL.version}:${EVAL.evaluated_through_turn}:${EVAL.live_finalized}:${EVAL.has_evaluation}`;
    await refreshEvaluation();
    if (`${EVAL.version}:${EVAL.evaluated_through_turn}:${EVAL.live_finalized}:${EVAL.has_evaluation}` !== before) {
      rebuildTrace(); renderRail(); renderTimeline(); renderSynthesis();
      if (statsOpen()) renderStats();
    }
    if (!EVAL.running) { clearInterval(evalPollTimer); evalPollTimer = null;
      rebuildTrace(); renderRail(); renderTimeline(); renderSynthesis();
      toast(EVAL.error ? "Evaluation paused: " + EVAL.error
        : EVAL.members?.some(m => m.status !== "complete") ? "Evaluation finished with issues — review the run status" : "Evaluation complete");
      if (statsOpen()) renderStats(); }
    renderEvalCta(); renderEvalBar(); }, 2500);
}

/* ================= SYNTHESIS ================= */
function evaluationIssuesHtml(issues) {
  if (!issues.length) return "";
  const scope = issues.filter(i => i.code === "screening_scope" || /^Unexpected or repeated message span \d+$/.test(i.explanation));
  const other = issues.filter(i => !scope.includes(i));
  const counts = new Map();
  for (const i of scope) counts.set(i.explanation, (counts.get(i.explanation) || 0) + 1);
  return '<details class="researchDiagnostics"><summary>Screening / analysis diagnostics · ' + issues.length + ' notices</summary>' +
    (scope.length ? '<p><strong>Screening response did not match its requested messages.</strong> Out-of-batch rows were ignored. Check message coverage below for missing work. These notices do not mean the API call failed.</p><ul>' +
      [...counts].map(([text,n]) => '<li>' + escapeHtml(text) + (n > 1 ? ' × ' + n : '') + '</li>').join('') + '</ul>' : '') +
    other.map(i => '<p><strong>' + escapeHtml(pretty(i.stage)) + ' issue:</strong> ' + escapeHtml(i.explanation) + '</p>').join('') + '</details>';
}
function renderSynthesis() {
  const section = el("synthSection");
  section.hidden = !EVAL.has_evaluation || !state.filters.showCommentary;
  if (section.hidden) return;
  const members = EVAL.members || [];
  const sourceWarning = EVAL.coverage && !EVAL.coverage.source_matches
    ? '<p class="researchCaution">Source mismatch: this evaluation cannot support counts for the current game.</p>' : "";
  const stale = EVAL.coverage?.partial ? '<p>' + (EVAL.live_evaluation && EVAL.running
    ? 'Evaluation is catching up with completed turns.'
    : 'Evaluation covers an earlier cutoff. New events require a new evaluation; previous outcomes remain tied to their original cutoff.') + '</p>' : "";
  section.innerHTML = '<span class="eyebrow">' + escapeHtml(EVAL.codebook_version || "") + '</span>' +
    sourceWarning + stale + members.map(m => '<p><strong>' + escapeHtml(m.label) + '</strong> · ' +
      escapeHtml(m.status) + ' · ' + m.episodes.length + ' commitments · ' + m.claims.length + ' claims' +
      ' · ' + m.coverage.filter(c => c.status !== 'missing').length + '/' + m.coverage.length + ' messages screened' + '</p>' +
      evaluationIssuesHtml(m.issues || [])).join("") +
    '<button class="btn dark" onclick="setMode(\'research\')">Read commitments & claims</button>' +
    '<p class="statNote">Each evaluator is an independent coder. Counts are not pooled across judges. Matching quotations establish source integrity, not correctness.</p>';
}
