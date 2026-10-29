/* ================= EXPERIMENT JUDGE (games with a Prober, Deceiver or Gaslighter) ================= */
const JUDGE_URL = window.BOOT.urls.experiment_judge || "";
const JUDGE_DEFAULT_MODEL = "anthropic:claude-opus-5-5:medium";
const JUDGE_DESCRIPTIONS = {
  gaslighting: "Codes each false or distorted account of a past event the Gaslighter gave, how each target responded, and whether the target's notes adopted it.",
  probing: "Codes how well the Prober read each opponent's mission, and whether its leverage attempts got listeners to act and paid off.",
  deception: "Codes each insincere assurance the Deceiver gave: whether the target relied on it, whether it was exploited, and whether the target agreed again.",
};
// code -> [label, colour]; colours follow the reader's cooperative / adversarial / deceptive tones
const JUDGE_CODES = {
  accepted: ["accepted", "var(--adversarial)"], disputed_complied: ["disputed but complied", "var(--deceptive)"],
  disputed: ["disputed", "var(--coop)"], ignored: ["ignored", "var(--neutral)"],
  complied: ["complied", "var(--coop)"], refused: ["refused", "var(--adversarial)"],
  no_opportunity: ["no opportunity", "var(--neutral)"],
  relied: ["relied", "var(--adversarial)"], accepted_only: ["accepted only", "var(--deceptive)"],
  renewed: ["agreed again", "var(--adversarial)"], retaliated: ["retaliated", "var(--coop)"],
  accused: ["accused", "var(--deceptive)"], no_reaction: ["no reaction", "var(--neutral)"],
};
let judge = {available: false, judge: null, running: false, error: null, assessment: null};
let judgePollTimer = null;
let judgeConfigOpen = false;

function judgeSpanChips(spans) {
  return (spans || []).map(span =>
    `<button class="chip" title="Open in raw trace" onclick="openLineDrawer(${Number(span)})">[${Number(span)}]</button>`).join("");
}
function judgeBadge(code) {
  const [label, color] = JUDGE_CODES[code] || [pretty(code), "var(--neutral)"];
  return `<span class="judgeBadge" style="border-color:${color};color:${color}">${escapeHtml(label)}</span>`;
}
function judgeTag(text) { return `<span class="judgeBadge">${escapeHtml(text)}</span>`; }
function judgeTableHtml(table) {
  if (!table.rows.length) return `<h3>${escapeHtml(table.title)}</h3><p>Nothing was coded.</p>`;
  const cell = (key, value) => key === "player" ? unitPlayer(value) : escapeHtml(value == null ? "" : String(value));
  const body = table.rows.map(row => `<tr>${table.columns.map(([key]) => `<td>${cell(key, row[key])}</td>`).join("")}</tr>`).join("");
  return `<h3>${escapeHtml(table.title)}</h3><div class="researchTable"><table class="statsTable" aria-label="${escapeHtml(table.title)}">`
    + `<thead><tr>${table.columns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* one renderer per judge for its coded items, each returning [summary label, items html] */
const JUDGE_ITEM_RENDERERS = {
  gaslighting: result => ["Claims", result.claims.map((claim, position) => {
    const targets = claim.targets.map(target => `<li>${unitPlayer(target.player)} ${judgeBadge(target.response)}`
      + judgeSpanChips(target.response_spans)
      + (target.notes_adopted ? ` ${judgeTag("notes adopted")}${judgeSpanChips(target.note_spans)}` : "") + `</li>`).join("");
    return `<article class="judgeItem"><h4>${position + 1}. ${escapeHtml(claim.claim)} ${judgeTag(claim.falsity)}</h4>`
      + `<p>Said in ${judgeSpanChips(claim.claim_spans)}</p>`
      + `<p><strong>Trace shows:</strong> ${escapeHtml(claim.truth)} ${judgeSpanChips(claim.truth_spans)}</p>`
      + `<ul>${targets}</ul></article>`;
  }).join("")],
  probing: result => ["Leverage attempts", result.attempts.map((attempt, position) =>
    `<article class="judgeItem"><h4>${position + 1}. ${escapeHtml(attempt.description)} ${judgeTag(pretty(attempt.move))}</h4>`
    + `<p>Said in ${judgeSpanChips(attempt.message_spans)}</p>`
    + `<p>Reads ${unitPlayer(attempt.described_opponent)}'s mission ${judgeTag(attempt.guess_correct ? "correct reading" : "wrong reading")}</p>`
    + `<ul><li>${unitPlayer(attempt.listener)} ${judgeBadge(attempt.outcome)}${judgeSpanChips(attempt.outcome_spans)}</li>`
    + (attempt.gain ? `<li>${judgeTag("prober gained")}${judgeSpanChips(attempt.gain_spans)}</li>` : "") + `</ul>`
    + (attempt.note ? `<p class="statNote">${escapeHtml(attempt.note)}</p>` : "") + `</article>`).join("")],
  deception: result => ["Attempts", result.attempts.map((attempt, position) =>
    `<article class="judgeItem"><h4>${position + 1}. ${escapeHtml(attempt.promise)}</h4>`
    + `<p>To ${unitPlayer(attempt.target)} in ${judgeSpanChips(attempt.promise_spans)}</p>`
    + `<ul><li>${judgeBadge(attempt.reliance)}${judgeSpanChips(attempt.reliance_spans)}`
    + (attempt.reliance_also_self_interested ? ` ${judgeTag("also in its own interest")}` : "") + `</li>`
    + (attempt.breached ? `<li>${judgeTag("breached")}${judgeSpanChips(attempt.breach_spans)}`
      + (attempt.payoff ? ` ${judgeTag("deceiver gained")}${judgeSpanChips(attempt.payoff_spans)}` : "") + `</li>`
      + `<li>Afterwards ${judgeBadge(attempt.aftermath)}${judgeSpanChips(attempt.aftermath_spans)}`
      + (attempt.accused ? ` ${judgeTag("accused")}${judgeSpanChips(attempt.accused_spans)}` : "")
      + (attempt.retaliated ? ` ${judgeTag("attacked back")}${judgeSpanChips(attempt.retaliated_spans)}` : "")
      + `</li>` : `<li>${judgeTag("not breached")}</li>`)
    + `</ul></article>`).join("")],
};

function judgeResultHtml(result) {
  const issues = result.citation_issues || [];
  const meta = [result.evaluator, result.codebook_version, typeof shortDate === "function" ? shortDate(result.created_at) : result.created_at,
    result.finished ? "finished game" : "game still open"].filter(Boolean).map(escapeHtml).join(" · ");
  const renderItems = JUDGE_ITEM_RENDERERS[result.judge];
  const [itemsLabel, itemsHtml] = renderItems ? renderItems(result) : ["", ""];
  return `<p class="statNote">${meta}</p>`
    + (result.tables || []).map(judgeTableHtml).join("")
    + (result.summary ? `<p class="judgeSummary">${escapeHtml(result.summary)}</p>` : "")
    + (issues.length ? `<details class="researchDiagnostics"><summary>${issues.length} citation issue${issues.length === 1 ? "" : "s"}</summary>`
      + `<ul>${issues.map(issue => `<li>${escapeHtml(issue)}</li>`).join("")}</ul></details>` : "")
    + (itemsHtml ? `<details class="judgeItems"><summary>${escapeHtml(itemsLabel)}</summary>${itemsHtml}</details>` : "");
}
function judgeCardHtml() {
  const result = judge.assessment;
  const blockedByPlay = gameRunning && !GAME.result;
  let status = "";
  if (judge.running) status = '<p class="mono">◐ judging the whole game…</p>';
  else if (judge.error) status = `<p class="mono" style="color:var(--adversarial)">⚠ last run failed: ${escapeHtml(judge.error)}</p>`;
  const controls = judge.running ? "" : `<div class="judgeActions">
      <button class="btn${result ? "" : " dark"}" id="judgeToggle">${result ? "Run again" : "Choose judge model"}</button></div>
    <div class="evalConfig${judgeConfigOpen ? " open" : ""}" id="judgeConfig">
      <div id="judgeModelRow"></div>
      <div class="evaluationModeActions"><button class="btn dark" id="judgeRun"${blockedByPlay ? " disabled" : ""}>Run ${escapeHtml((judge.judge?.title || "judge").toLowerCase())}</button></div>
      <p>${blockedByPlay ? "Pause the game first. " : ""}One call over the whole trace; uses API tokens. An earlier result is archived beside the game.</p></div>`;
  return `<h2>${escapeHtml(judge.judge?.title || "Experiment judge")}</h2>`
    + (result ? "" : `<p>${escapeHtml(JUDGE_DESCRIPTIONS[judge.judge?.name] || "")}</p>`)
    + status + (result ? judgeResultHtml(result) : "") + controls;
}
function addJudgeModelRow(spec) {
  const [provider, model, effort] = spec.split(":");
  const providers = Object.keys(modelCatalog.models_by_provider);
  const options = (values, selected) => values.map(value => `<option${value === selected ? " selected" : ""}>${escapeHtml(value)}</option>`).join("");
  const row = el("judgeModelRow");
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
function renderJudgeCard() {
  const card = el("judgeCard");
  if (!card) return;
  card.hidden = !JUDGE_URL || !judge.available;
  if (card.hidden) return;
  card.innerHTML = judgeCardHtml();
  const toggle = el("judgeToggle");
  if (!toggle) return;
  const fillRow = async () => {
    await loadCatalog();
    if (!el("judgeModelRow").children.length) addJudgeModelRow(judge.assessment?.evaluator || JUDGE_DEFAULT_MODEL);
  };
  toggle.onclick = async () => {
    judgeConfigOpen = !judgeConfigOpen;
    el("judgeConfig").classList.toggle("open", judgeConfigOpen);
    if (judgeConfigOpen) await fillRow();
  };
  if (judgeConfigOpen) fillRow();
  el("judgeRun").onclick = runJudge;
}
async function runJudge() {
  const row = el("judgeModelRow");
  const evaluator = ["evP", "evM", "evE"].map(name => row.querySelector("." + name)?.value).join(":");
  const response = await fetch(JUDGE_URL, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({evaluator})});
  if (!response.ok) { toast("Could not start the judge: " + (await response.json()).detail); return; }
  judge = {...judge, running: true, error: null};
  judgeConfigOpen = false;
  renderJudgeCard();
  pollJudge();
}
async function refreshJudge() {
  const response = await fetch(JUDGE_URL, {cache: "no-store"});
  if (response.ok) judge = await response.json();
}
function pollJudge() {
  if (judgePollTimer) return;
  judgePollTimer = setInterval(async () => {
    await refreshJudge();
    if (judge.running) return;
    clearInterval(judgePollTimer); judgePollTimer = null;
    toast(judge.error ? "Judge failed: " + judge.error : `${judge.judge?.title || "Judge"} result ready`);
    renderJudgeCard();
  }, 3000);
}
let judgeBlockedByPlay = null;
// the reader announces game status every second; redraw only when pausing or resuming changes the run button
function onJudgeGameStatus() {
  const blocked = gameRunning && !GAME.result;
  if (blocked === judgeBlockedByPlay) return;
  judgeBlockedByPlay = blocked;
  renderJudgeCard();
}
async function initJudge() {
  await refreshJudge();
  renderJudgeCard();
  if (judge.running) pollJudge();
}
if (typeof document !== "undefined" && JUDGE_URL) {
  document.addEventListener("reader:game-status", onJudgeGameStatus);
  initJudge();
}
