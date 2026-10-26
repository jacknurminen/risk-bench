/* ================= EVALUATE (configure + run the panel) ================= */
let modelCatalog = null;
async function loadCatalog() { if (modelCatalog) return modelCatalog; modelCatalog = await (await fetch("/api/models")).json(); return modelCatalog; }
function evaluationMethodLabel(method) {
  return method === 'live_rounds' ? 'Live round evaluation' : 'Whole-game analysis';
}
let evaluationDraftMethod = null;
function chosenEvaluationMethod() {
  return evaluationDraftMethod || EVAL.evaluation_method ||
    (EVAL.configured_panels?.whole_game?.length ? 'whole_game' : 'live_rounds');
}
function evaluationRunLabel(method) {
  return method === 'whole_game' ? 'Run whole-game analysis'
    : gameRunning && !GAME.result ? 'Start live evaluation' : 'Replay rounds';
}
function evaluationMethodHint(method) {
  return method === 'whole_game'
    ? (GAME.result ? 'Assess the full game afresh.' : 'Whole-game analysis is available once the game finishes.')
    : 'Review rounds in order, then review the ending. Replay starts a new version from round one.';
}
function evaluationConfigHtml() {
  const method = chosenEvaluationMethod();
  return `<div class="evalConfig" id="evalConfig">
    <fieldset class="evaluationMethodToggle"><legend>Evaluation method</legend>
      <label><input type="radio" name="evaluationMethod" value="live_rounds"${method === 'live_rounds' ? ' checked' : ''}><span>Live</span></label>
      <label><input type="radio" name="evaluationMethod" value="whole_game"${method === 'whole_game' ? ' checked' : ''}><span>Whole game</span></label>
    </fieldset>
    <p id="evaluationMethodHint">${evaluationMethodHint(method)}</p>
    <div id="evalRows"></div>
    <div class="evaluationModeActions"><button class="btn" id="evalAdd">+ add member</button>
      <button class="btn dark" id="evalRun"${method === 'whole_game' && !GAME.result ? ' disabled' : ''}>${evaluationRunLabel(method)}</button></div>
    <p>Runs use API tokens. Previous results remain in the version selector.</p></div>`;
}
function bindEvaluationConfig() {
  el('evalAdd').onclick = () => addEvalRow();
  el('evalRun').onclick = () => runEvaluation();
  el('evalConfig').querySelectorAll('input[name="evaluationMethod"]').forEach(input => {
    input.onchange = () => {
      evaluationDraftMethod = input.value;
      el('evalRun').textContent = evaluationRunLabel(input.value);
      el('evalRun').disabled = input.value === 'whole_game' && !GAME.result;
      el('evaluationMethodHint').textContent = evaluationMethodHint(input.value);
      [...el('evalRows').children].forEach(syncEvalAgentToggle);
    };
  });
}
function prefillEvaluationPanel() {
  if (el('evalRows').children.length) return;
  const method = chosenEvaluationMethod();
  const configured = EVAL.configured_panels || {};
  const panel = [configured[method], (EVAL.versions || []).find(v => v.method === method)?.panel,
    configured.live_rounds, configured.whole_game].find(panel => panel?.length) || [];
  panel.forEach(spec => addEvalRow(spec));
  if (!panel.length) addEvalRow();
}
function renderEvalCta() {
  const cta = el("evalCta");
  if (!EVALUATE_URL || EVAL.has_evaluation || EVAL.live_evaluation) {
    cta.hidden = true; cta.innerHTML = ""; return;
  }
  cta.hidden = false;
  if (EVAL.running) {
    cta.innerHTML = `<span class="mono">${evalProgressHtml()}</span>`;
    return;
  }
  cta.innerHTML = `<h2>Research evaluation</h2>
    <p>Choose live round evaluation or whole-game analysis.</p>
    ${interruptedNoteHtml()}<button class="btn dark" id="evalToggle">Choose evaluators</button>
    ${evaluationConfigHtml()}`;
  el("evalToggle").onclick = async () => { await loadCatalog(); el("evalConfig").classList.toggle("open"); prefillEvaluationPanel(); };
  bindEvaluationConfig();
}

function evaluationHasPendingWork() {
  const rounds = [...new Set((EVAL.spans || []).filter(s => s.turn > 0 && s.round != null)
    .map(s => s.round))].sort((a,b) => a-b);
  if (!GAME.result) rounds.pop();
  return (rounds.at(-1) || 0) > (EVAL.evaluated_through_round || 0) || (!!GAME.result && !EVAL.live_finalized);
}
/* During play, reviews stay in the timeline. Paused games can replay completed rounds. */
function renderEvalBar() {
  const bar = el("evalBar");
  const versions = EVAL.versions || [];
  if ((!GAME.result && gameRunning) || !EVAL.has_evaluation || !versions.length) {
    bar.hidden = true; bar.innerHTML = ""; return;
  }
  bar.hidden = false;
  const current = versions.find(v => v.is_current) || versions[0];
  const viewing = selectedVersion || current.run_id;  // "" follows the current run
  const options = versions.map(v =>
    `<option value="${v.is_current ? "" : v.run_id}"${v.run_id === viewing ? " selected" : ""}>${escapeHtml(versionLabel(v))}</option>`).join("");
  const runControl = EVAL.running
    ? `<span class="mono versionMeta">${evalProgressHtml()}</span>`
    : `${interruptedNoteHtml()}${EVALUATE_URL ?
      ((!selectedVersion && EVAL.live_evaluation && !EVAL.live_finalized && evaluationHasPendingWork()
        ? '<button class="btn" id="resumeEvalBtn">▶ Resume evaluation</button>' : '')
        + '<button class="btn" id="rerunBtn">Configure evaluations</button>') : ""}`;
  bar.innerHTML = `<div class="evalBarRow">
      <span class="eyebrow">Evaluation</span>
      <select class="versionSelect" id="versionSelect">${options}</select>
      <span class="versionMeta">${versions.length} version${versions.length > 1 ? "s" : ""}</span>
      <span class="versionMeta">${escapeHtml(evaluationStatusLabel())}</span>
      <span style="flex:1"></span>${runControl}</div>
    ${evaluationConfigHtml()}`;
  el("versionSelect").onchange = e => { selectedVersion = e.target.value; switchVersion(); };
  const rerun = el("rerunBtn");
  if (rerun) rerun.onclick = async () => { await loadCatalog(); el("evalConfig").classList.toggle("open"); prefillEvaluationPanel(); };
  bindEvaluationConfig();
  el("resumeEvalBtn") && (el("resumeEvalBtn").onclick = resumeLiveReview);
}
/* Stored-game runs use the growing eval trace for progress. */
function evalProgressHtml() {
  const p = EVAL.progress;
  if (!p || p.state !== "running") return EVAL.live_evaluation ? "◐ reviewing recorded rounds…" : "◐ analyzing the whole game…";
  const cost = p.cost_usd != null ? ` · ~$${p.cost_usd.toFixed(2)}` : "";
  const failed = p.failed_calls ? ` · <span style="color:var(--adversarial)">${p.failed_calls} failed</span>` : "";
  const members = (p.members || []).map(m => `${m.spec.split(":")[0]} ${m.calls}/${m.expected_calls}`).join(", ");
  const method = EVAL.live_evaluation ? 'reviewing rounds' : 'analyzing the whole game';
  return `◐ ${method} · ${p.completed_calls}/${p.expected_calls} calls${cost}${failed}`
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
function evaluationStatusLabel() {
  const status = EVAL.completion_status;
  const flags = EVAL.review_flags || 0;
  const label = {complete:'Evaluation complete', unfinished:'Evaluation unfinished', interrupted:'Evaluation interrupted',
    running:'Evaluation running', not_started:'Not evaluated'}[status] || '';
  return label + (flags ? ' · ' + flags + ' annotation(s) need review' : '');
}

function versionLabel(v) {
  const panel = ((v.labels && v.labels.length ? v.labels : v.panel) || []).join(" │ ") || "evaluation";
  return `${evaluationMethodLabel(v.method)} · ${panel} · ${v.schema_version >= 6 ? "commitments & deception v4" : v.schema_version >= 4 ? "two-tag v3" : "legacy v2"} · ${shortDate(v.created_at)}${v.is_current ? " · current" : ""}`;
}
function shortDate(iso) {
  const d = new Date(iso);
  return isNaN(d) ? (iso || "") : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
async function switchVersion() {
  await refreshEvaluation(); rebuildTrace();
  if (state.drawer || state.unitDrawer) renderPanels();
  renderRail(); renderTimeline(); renderSynthesis(); renderEvalBar();
  if (typeof statsOpen === "function" && statsOpen()) renderStats();
}
function addEvalRow(spec) {
  const providers = Object.keys(modelCatalog.models_by_provider);
  const asAgent = (spec || "").startsWith("agent:");
  const parts = (spec || "").replace(/^agent:/, "").split(":");  // [agent:]provider:model:effort
  const row = document.createElement("div"); row.className = "evalRow";
  const opt = (arr, sel) => arr.map(v => `<option${v === sel ? " selected" : ""}>${v}</option>`).join("");
  const modelOpt = (models, sel) => models.map(m =>
    `<option value="${m.id}"${m.id === sel ? " selected" : ""}>${m.id} · $${m.input_usd_per_million}/$${m.output_usd_per_million}</option>`).join("");
  row.innerHTML = `<select class="evP">${opt(providers, parts[0])}</select><select class="evM"></select><select class="evE">${opt(modelCatalog.efforts, parts[2])}</select>
    <label class="evA"><input type="checkbox"${asAgent ? " checked" : ""}> run as agent</label>
    <button class="btn" onclick="this.parentNode.remove()">✕</button>`;
  el("evalRows").appendChild(row);
  const syncModels = sel => { const models = modelCatalog.models_by_provider[row.querySelector(".evP").value] ?? [];
    row.querySelector(".evM").innerHTML = modelOpt(models, sel); };
  row.querySelector(".evP").onchange = () => { syncModels(); syncEvalAgentToggle(row); };
  syncModels(parts[1]); syncEvalAgentToggle(row);
}
/* The agent judge is an Anthropic option and reads whole games only. */
function syncEvalAgentToggle(row) {
  const live = chosenEvaluationMethod() === "live_rounds";
  const toggle = row.querySelector(".evA");
  toggle.hidden = row.querySelector(".evP").value !== "anthropic";
  toggle.querySelector("input").disabled = live;
  toggle.title = live ? "Agent judges analyze whole games only. Choose Whole game to use one."
    : "Annotate the game with an agent that reads the trace and writes each record through checked tools.";
}
function evalRowSpec(row, method) {
  const provider = row.querySelector(".evP").value;
  const spec = `${provider}:${row.querySelector(".evM").value}:${row.querySelector(".evE").value}`;
  const asAgent = provider === "anthropic" && method === "whole_game" && row.querySelector(".evA input")?.checked;
  return asAgent ? `agent:${spec}` : spec;
}
async function runEvaluation() {
  const method = chosenEvaluationMethod();
  const live = method === "live_rounds";
  const rows = [...el("evalRows").children].map(row => evalRowSpec(row, method));
  if (!rows.length) { toast("add at least one panel member"); return; }
  const res = await fetch(EVALUATE_URL, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ evaluators: rows, live, restart: live && (!!GAME.result || !gameRunning) }) });
  if (!res.ok) { toast("could not start: " + (await res.json()).detail); return; }
  EVAL.configured_panels = {live_rounds: [], whole_game: [], [method]: rows};
  selectedVersion = "";  // follow the newest run so this fresh evaluation becomes what we view
  EVAL.error = null; EVAL.running = true; EVAL.live_evaluation = live; EVAL.review_active = false;
  renderEvalCta(); renderEvalBar();
  pollEvaluation();
}

async function resumeLiveReview() {
  const evaluators = EVAL.live_panel?.length ? EVAL.live_panel :
    (EVAL.versions || []).find(version => version.is_current)?.panel;
  if (!EVALUATE_URL || !evaluators?.length) { toast("No live evaluator panel is available to resume"); return; }
  const response = await fetch(EVALUATE_URL, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({evaluators, live: true})});
  if (!response.ok) { toast("Could not resume review: " + (await response.json()).detail); return; }
  selectedVersion = "";
  EVAL.error = null; EVAL.running = true; EVAL.review_active = false;
  renderTimeline(); renderEvalBar(); pollEvaluation();
}

/* poll while a run is in flight (also picked up on page load when a run is already going) */
let evalPollTimer = null;
function pollEvaluation() {
  if (evalPollTimer) return;  // one poller is enough
  evalPollTimer = setInterval(async () => {
    const before = `${EVAL.version}:${EVAL.evaluated_through_round}:${EVAL.evaluated_through_turn}:${EVAL.live_finalized}:${EVAL.has_evaluation}:${EVAL.review_active}:${EVAL.reviewing_round}:${EVAL.reviewing_final}`;
    await refreshEvaluation();
    if (`${EVAL.version}:${EVAL.evaluated_through_round}:${EVAL.evaluated_through_turn}:${EVAL.live_finalized}:${EVAL.has_evaluation}:${EVAL.review_active}:${EVAL.reviewing_round}:${EVAL.reviewing_final}` !== before) {
      rebuildTrace(); renderRail(); renderTimeline(); renderSynthesis();
      if (statsOpen()) renderStats();
    }
    const followingLiveReview = LIVE && gameRunning && !GAME.result && EVAL.live_evaluation && !EVAL.live_finalized && !EVAL.error;
    if (!EVAL.running && !followingLiveReview) { clearInterval(evalPollTimer); evalPollTimer = null;
      rebuildTrace(); renderRail(); renderTimeline(); renderSynthesis();
      if (EVAL.error) toast("Evaluation paused: " + EVAL.error);
      else if (!EVAL.live_evaluation || EVAL.live_finalized) toast(evaluationStatusLabel() || "Evaluation complete");
      else if (!evaluationHasPendingWork()) toast("Completed rounds reviewed");
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
  section.hidden = EVAL.live_evaluation || !EVAL.has_evaluation || !state.filters.showCommentary;
  if (section.hidden) return;
  const members = EVAL.members || [];
  const sourceWarning = EVAL.coverage && !EVAL.coverage.source_matches
    ? '<p class="researchCaution">Source mismatch: this evaluation cannot support counts for the current game.</p>' : "";
  const waitingForRound = EVAL.live_evaluation && EVAL.running && !EVAL.review_active;
  const stale = EVAL.coverage?.partial && !waitingForRound ? '<p>' + (EVAL.live_evaluation && EVAL.running
    ? 'Evaluation is catching up with completed rounds.'
    : 'Evaluation covers an earlier cutoff. New events require a new evaluation; previous outcomes remain tied to their original cutoff.') + '</p>' : "";
  section.innerHTML = '<span class="eyebrow">' + escapeHtml(EVAL.codebook_version || "") + '</span>' +
    sourceWarning + stale + members.map(m => '<p><strong>' + escapeHtml(m.label) + '</strong> · ' +
      escapeHtml(m.status) + ' · ' + (m.commitments || m.episodes || []).length + ' commitments · ' +
      (m.deceptions || m.statements || m.claims || []).length + (EVAL.tag_schema === 'commitments_deception_v4'
        ? ' documented deceptions' : EVAL.tag_schema === 'two_tag_v3' ? ' statements' : ' claims') +
      ' · ' + m.coverage.filter(c => c.status !== 'missing').length + '/' + m.coverage.length + ' messages screened' + '</p>' +
      evaluationIssuesHtml(m.issues || [])).join("") +
    '<button class="btn dark" onclick="setMode(\'research\')">Read research tags</button>' +
    '<p class="statNote">Each evaluator is an independent coder. Counts are not pooled across judges. Matching quotations establish source integrity, not correctness.</p>';
}
