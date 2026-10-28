/* ================= GASLIGHTING JUDGE (games with a Gaslighter player) ================= */
const GASLIGHTING_URL = window.BOOT.urls.gaslighting || "";
const GASLIGHTING_DEFAULT_JUDGE = "anthropic:claude-opus-5-5:medium";
const GASLIGHTING_RESPONSES = [
  ["accepted", "accepted", "var(--adversarial)"],
  ["disputed_complied", "disputed but complied", "var(--deceptive)"],
  ["disputed", "disputed", "var(--coop)"],
  ["ignored", "ignored", "var(--neutral)"],
];
let gaslighting = {running: false, error: null, assessment: null};
let gaslightingPollTimer = null;
let gaslightingConfigOpen = false;

function gaslightingSpanChips(spans) {
  return (spans || []).map(span =>
    `<button class="chip" title="Open in raw trace" onclick="openLineDrawer(${Number(span)})">[${Number(span)}]</button>`).join("");
}
function gaslightingResponseBadge(code) {
  const [, label, color] = GASLIGHTING_RESPONSES.find(([key]) => key === code) || [code, pretty(code), "var(--neutral)"];
  return `<span class="gaslightingBadge" style="border-color:${color};color:${color}">${escapeHtml(label)}</span>`;
}
function gaslightingTableHtml(rows) {
  if (!rows.length) return '<p>No targets were coded.</p>';
  const head = ["player", "model", "claims", ...GASLIGHTING_RESPONSES.map(([, label]) => label), "notes adopted"];
  const body = rows.map(row => `<tr><td>${unitPlayer(row.player)}</td><td class="mono">${escapeHtml(row.model || "")}</td>`
    + `<td>${row.claims_received}</td>` + GASLIGHTING_RESPONSES.map(([key]) => `<td>${row[key]}</td>`).join("")
    + `<td>${row.notes_adopted}</td></tr>`).join("");
  return `<div class="researchTable"><table class="statsTable" aria-label="Gaslighting outcomes by target">`
    + `<thead><tr>${head.map(label => `<th>${escapeHtml(label)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function gaslightingClaimHtml(claim, number) {
  const targets = claim.targets.map(target => `<li>${unitPlayer(target.player)} ${gaslightingResponseBadge(target.response)}`
    + `${gaslightingSpanChips(target.response_spans)}`
    + (target.notes_adopted ? ` <span class="gaslightingBadge">notes adopted</span>${gaslightingSpanChips(target.note_spans)}` : "")
    + `</li>`).join("");
  return `<article class="gaslightingClaim"><h4>${number}. ${escapeHtml(claim.claim)} <span class="gaslightingBadge">${escapeHtml(claim.falsity)}</span></h4>`
    + `<p>Said in ${gaslightingSpanChips(claim.claim_spans)}</p>`
    + `<p><strong>Trace shows:</strong> ${escapeHtml(claim.truth)} ${gaslightingSpanChips(claim.truth_spans)}</p>`
    + `<ul>${targets}</ul></article>`;
}
function gaslightingResultHtml(result) {
  const issues = result.citation_issues || [];
  const meta = [result.evaluator, result.codebook_version, typeof shortDate === "function" ? shortDate(result.created_at) : result.created_at,
    result.finished ? "finished game" : "game still open"].filter(Boolean).map(escapeHtml).join(" · ");
  return `<p class="statNote">${meta}</p>`
    + `<p>${result.claims.length} false or distorted claim${result.claims.length === 1 ? "" : "s"} by ${unitPlayer(result.gaslighter)}.</p>`
    + gaslightingTableHtml(result.by_target || [])
    + (result.summary ? `<p class="gaslightingSummary">${escapeHtml(result.summary)}</p>` : "")
    + (issues.length ? `<details class="researchDiagnostics"><summary>${issues.length} citation issue${issues.length === 1 ? "" : "s"}</summary>`
      + `<ul>${issues.map(issue => `<li>${escapeHtml(issue)}</li>`).join("")}</ul></details>` : "")
    + (result.claims.length ? `<details class="gaslightingClaims"><summary>Claims</summary>`
      + result.claims.map((claim, position) => gaslightingClaimHtml(claim, position + 1)).join("") + `</details>` : "");
}
function gaslightingCardHtml() {
  const result = gaslighting.assessment;
  const blockedByPlay = gameRunning && !GAME.result;
  let status = "";
  if (gaslighting.running) status = '<p class="mono">◐ judging the whole game…</p>';
  else if (gaslighting.error) status = `<p class="mono" style="color:var(--adversarial)">⚠ last run failed: ${escapeHtml(gaslighting.error)}</p>`;
  const controls = gaslighting.running ? "" : `<div class="gaslightingActions">
      <button class="btn${result ? "" : " dark"}" id="gaslightingToggle">${result ? "Run again" : "Choose judge"}</button></div>
    <div class="evalConfig${gaslightingConfigOpen ? " open" : ""}" id="gaslightingConfig">
      <div id="gaslightingJudgeRow"></div>
      <div class="evaluationModeActions"><button class="btn dark" id="gaslightingRun"${blockedByPlay ? " disabled" : ""}>Run gaslighting judge</button></div>
      <p>${blockedByPlay ? "Pause the game first. " : ""}One call over the whole trace; uses API tokens. An earlier result is archived beside the game.</p></div>`;
  return `<h2>Gaslighting judge</h2>`
    + (result ? "" : "<p>Codes each false or distorted account of a past event the Gaslighter gave, how each target responded, and whether the target's notes adopted it.</p>")
    + status + (result ? gaslightingResultHtml(result) : "") + controls;
}
function addGaslightingJudgeRow(spec) {
  const [provider, model, effort] = spec.split(":");
  const providers = Object.keys(modelCatalog.models_by_provider);
  const options = (values, selected) => values.map(value => `<option${value === selected ? " selected" : ""}>${escapeHtml(value)}</option>`).join("");
  const row = el("gaslightingJudgeRow");
  row.className = "evalRow";
  row.innerHTML = `<select class="evP">${options(providers, provider)}</select><select class="evM"></select>`
    + `<select class="evE">${options(modelCatalog.efforts, effort)}</select>`;
  const syncModels = selected => {
    const models = modelCatalog.models_by_provider[row.querySelector(".evP").value] ?? [];
    row.querySelector(".evM").innerHTML = models.map(m =>
      `<option value="${escapeHtml(m.id)}"${m.id === selected ? " selected" : ""}>${escapeHtml(m.id)} · $${m.input_usd_per_million}/$${m.output_usd_per_million}</option>`).join("");
  };
  row.querySelector(".evP").onchange = () => syncModels();
  syncModels(model);
}
function renderGaslightingCard() {
  const card = el("gaslightingCard");
  if (!card) return;
  card.hidden = !GASLIGHTING_URL;
  if (card.hidden) return;
  card.innerHTML = gaslightingCardHtml();
  const toggle = el("gaslightingToggle");
  if (!toggle) return;
  const fillRow = async () => {
    await loadCatalog();
    if (!el("gaslightingJudgeRow").children.length)
      addGaslightingJudgeRow(gaslighting.assessment?.evaluator || GASLIGHTING_DEFAULT_JUDGE);
  };
  toggle.onclick = async () => {
    gaslightingConfigOpen = !gaslightingConfigOpen;
    el("gaslightingConfig").classList.toggle("open", gaslightingConfigOpen);
    if (gaslightingConfigOpen) await fillRow();
  };
  if (gaslightingConfigOpen) fillRow();
  el("gaslightingRun").onclick = runGaslightingJudge;
}
async function runGaslightingJudge() {
  const row = el("gaslightingJudgeRow");
  const evaluator = ["evP", "evM", "evE"].map(name => row.querySelector("." + name)?.value).join(":");
  const response = await fetch(GASLIGHTING_URL, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({evaluator})});
  if (!response.ok) { toast("Could not start the gaslighting judge: " + (await response.json()).detail); return; }
  gaslighting = {...gaslighting, running: true, error: null};
  gaslightingConfigOpen = false;
  renderGaslightingCard();
  pollGaslighting();
}
async function refreshGaslighting() {
  const response = await fetch(GASLIGHTING_URL, {cache: "no-store"});
  if (response.ok) gaslighting = await response.json();
}
function pollGaslighting() {
  if (gaslightingPollTimer) return;
  gaslightingPollTimer = setInterval(async () => {
    await refreshGaslighting();
    if (gaslighting.running) return;
    clearInterval(gaslightingPollTimer); gaslightingPollTimer = null;
    toast(gaslighting.error ? "Gaslighting judge failed: " + gaslighting.error : "Gaslighting assessment ready");
    renderGaslightingCard();
  }, 3000);
}
async function initGaslighting() {
  await refreshGaslighting();
  renderGaslightingCard();
  if (gaslighting.running) pollGaslighting();
}
let gaslightingBlockedByPlay = null;
// the reader announces game status every second; redraw only when pausing or resuming changes the run button
function onGaslightingGameStatus() {
  const blocked = gameRunning && !GAME.result;
  if (blocked === gaslightingBlockedByPlay) return;
  gaslightingBlockedByPlay = blocked;
  renderGaslightingCard();
}
if (typeof document !== "undefined" && GASLIGHTING_URL) {
  document.addEventListener("reader:game-status", onGaslightingGameStatus);
  initGaslighting();
}
