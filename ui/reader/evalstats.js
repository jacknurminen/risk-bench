/* ================= STATS MODAL: EVALUATION SHEET ================= */
/* The evaluator panel's own statistics, following the version selector: the commentary-style
   cumulative-valence charts over the panel's tags (machinery shared via stats.js panelContext),
   per-player tag chips, the run's API usage/cost from its own trace, and the analysis artifact
   (consideration coverage, agreement, calibration, behaviour inventory, divergences) visualized. */

const evaluationPanel = panelContext({ toggles: "evalMemberToggles", filter: "evalAgreementFilter", charts: "evalValenceCharts" });
let evalUsage = null, evalUsageVersion = false;      // false = never fetched (null is a valid version)
let evalAnalysis = null, evalAnalysisVersion = false;

const evalMembers = () => (EVAL.members || []).map(m => ({ index: m.index, label: m.label }));
/* the evaluator's tags regrouped into the shared stream shape: one entry per (member, turn);
   each tag keeps its resolved cited spans so the span locus grain can cluster by overlap */
function evalTagStream() {
  const stream = [];
  (EVAL.members || []).forEach(m => {
    const byTurn = new Map();
    (m.tags || []).forEach(t => {
      (byTurn.get(t.turn) ?? byTurn.set(t.turn, []).get(t.turn)).push({
        player: t.player, behavior: t.behavior,
        spans: (t.evidence || []).filter(e => e.resolved).map(e => e.span),
      });
    });
    byTurn.forEach((tags, turn) => stream.push({ turn, member: m.index, tags }));
  });
  return stream;
}

function renderEvalStats() {
  const sheet = el("evalSheet");
  if (!EVAL.has_evaluation) {
    sheet.innerHTML = `<div class="empty" style="margin-top:14px">${EVAL.running
      ? "The panel is still running — its statistics appear when the run lands."
      : "This game has no evaluation yet — run the panel from the page above."}</div>`;
    return;
  }
  sheet.innerHTML = `
    <div id="analysisCards" class="statCards"></div>
    <div style="margin-top:14px" id="evalGrainControls" class="grainControls"></div>
    <div id="evalMemberToggles"></div>
    <div id="evalAgreementFilter"></div>
    <div id="evalValenceCharts"></div>
    <div id="evalTagFilterNote"></div>
    <table id="evalTagTable" class="statsTable"></table>
    <div class="eyebrow" style="margin:18px 0 0">panel usage — this run's API calls, from its trace</div>
    <table id="evalUsageTable" class="statsTable"></table>
    <div id="evalUsageNote" class="statNote">Costs are estimated from list prices per token; unknown models show no cost. The full calls are in the API calls tab (source: evaluator panel).</div>
    <div id="analysisSections"></div>`;
  syncPanel(evaluationPanel, evalMembers(), evalTagStream());
  renderEvalGrainControls();
  renderPanelToggles(evaluationPanel, "evaluator panel — line is the mean, band is the spread:",
    () => renderPanelValenceCharts(evaluationPanel));
  renderPanelAgreementFilter(evaluationPanel, () => {
    renderPanelValenceCharts(evaluationPanel);
    renderEvalTagTable();
    renderAnalysis();  // the inventory bars honor the agreement filter too
  });
  renderPanelValenceCharts(evaluationPanel);
  renderEvalTagTable();
  renderEvalUsage();
  renderAnalysis();
}

/* the two aggregation-grain selectors: pure view-layer rollups over the same tags — a grain
   change regroups what was already annotated, it never re-runs the panel */
function renderEvalGrainControls() {
  const container = el("evalGrainControls");
  const select = (id, options, value) =>
    `<select id="${id}">${options.map(o => `<option${o === value ? " selected" : ""}>${o}</option>`).join("")}</select>`;
  const clampNote = evaluationPanel.codeGrain === "family" || evaluationPanel.codeGrain === "axis"
    ? `<span style="color:var(--mute-2)">· ${evaluationPanel.codeGrain} cells stack several codes, so agreement still matches at behavior grain</span>` : "";
  container.innerHTML = `<span>aggregation grain — regroup the same tags, no re-run:</span>
    <label>group by ${select("evalCodeGrainSelect", CODE_GRAINS, evaluationPanel.codeGrain)}</label>
    <label>locate by ${select("evalLocusGrainSelect", LOCUS_GRAINS, evaluationPanel.locusGrain)}</label>${clampNote}`;
  el("evalCodeGrainSelect").onchange = e => { evaluationPanel.codeGrain = e.target.value; renderEvalGrainDependents(); };
  el("evalLocusGrainSelect").onchange = e => { evaluationPanel.locusGrain = e.target.value; renderEvalGrainDependents(); };
}

function renderEvalGrainDependents() {
  renderEvalGrainControls();
  renderPanelValenceCharts(evaluationPanel);  // charts are per-turn by nature; only the code grain regroups them
  renderEvalTagTable();
  renderAnalysis();
}

function renderEvalTagTable() {
  const tagsBySeat = panelFilteredTableTags(evaluationPanel);
  const grainNote = evaluationPanel.codeGrain === "behavior" && evaluationPanel.locusGrain === "turn"
    ? "" : ` (grouped by ${evaluationPanel.codeGrain}, located by ${evaluationPanel.locusGrain})`;
  el("evalTagFilterNote").textContent = panelTagFilterNote(evaluationPanel);
  el("evalTagTable").innerHTML = `<thead><tr><th>player</th><th>behaviour tags — the evaluator panel's read${grainNote}</th></tr></thead><tbody>` +
    GAME.players.map((p, seat) =>
      `<tr><td class="mono" style="color:${COLORS[seat]};font-weight:600">${tagOf(p.name)}</td>
        <td>${tagChips(tagsBySeat[seat], evaluationPanel.members.length, evaluationPanel.codeGrain) || "—"}</td></tr>`).join("") + "</tbody>";
}

/* ---- panel usage: per-member calls, tokens, and estimated cost from the run's own trace ---- */
async function renderEvalUsage() {
  if (!EVAL_POLL_URL) { el("evalUsageNote").textContent = "Usage is not available in a static export."; return; }
  const version = EVAL.version;
  if (evalUsageVersion !== version) {
    const response = await fetch(`${EVAL_POLL_URL}/usage${version ? `?version=${encodeURIComponent(version)}` : ""}`, { cache: "no-store" });
    evalUsage = response.ok ? await response.json() : null;
    evalUsageVersion = version;
  }
  const table = el("evalUsageTable");  // looked up after the await: the sheet may have re-rendered
  if (!table) return;
  if (!evalUsage || !evalUsage.members.length) {
    table.innerHTML = "";
    el("evalUsageNote").textContent = "No evaluator API calls are recorded for this run.";
    return;
  }
  const fmtCost = c => c == null ? "—" : `$${c.toFixed(2)}`;
  const row = (label, color, entry) => {
    const failed = entry.errors ? ` <span style="color:var(--adversarial)">(${entry.errors} failed)</span>` : "";
    return `<tr><td class="mono" style="color:${color};font-weight:600">${label}</td>
      <td class="mono" style="font-size:11px">${escapeHtml(entry.model ?? "")}</td>
      <td class="mono" style="font-size:11px">${entry.calls}${failed}</td>
      <td class="mono" style="font-size:11px">${entry.input_tokens.toLocaleString("en-US")} / ${entry.output_tokens.toLocaleString("en-US")}</td>
      <td class="mono" style="font-size:11px">${fmtCost(entry.cost_usd)}</td></tr>`;
  };
  table.innerHTML = `<thead><tr><th>member</th><th>model</th><th>api calls</th><th>tokens in / out</th><th>est. cost</th></tr></thead><tbody>`
    + evalUsage.members.map(m => row(escapeHtml(annotatorLabel(m.index)), annotatorColor(m.index), m)).join("")
    + row("total", "var(--ink)", { ...evalUsage.total, model: "" })
    + "</tbody>";
}

/* ---- the analysis artifact, fetched per version and rendered as cards + sections ---- */
async function renderAnalysis() {
  if (!ANALYSIS_URL) return;
  const version = EVAL.version;
  if (evalAnalysisVersion !== version) {
    const response = await fetch(`${ANALYSIS_URL}${version ? `?version=${encodeURIComponent(version)}` : ""}`, { cache: "no-store" });
    evalAnalysis = response.ok ? await response.json() : null;
    evalAnalysisVersion = version;
  }
  const cards = el("analysisCards"), sections = el("analysisSections");
  if (!cards || !sections) return;  // the sheet re-rendered mid-fetch; the newer call fills it
  if (!evalAnalysis) { sections.innerHTML = '<div class="empty">The analysis artifact could not be loaded.</div>'; return; }
  cards.innerHTML = analysisCardsHtml(evalAnalysis);
  sections.innerHTML = [
    considerationGridHtml(),
    agreementHtml(evalAnalysis),
    calibrationHtml(evalAnalysis),
    inventoryHtml(evalAnalysis),
    leniencyHtml(evalAnalysis),
    divergencesHtml(evalAnalysis),
  ].join("");
}

const pct = fraction => `${Math.round(fraction * 100)}%`;
const statCard = (value, label, sub) =>
  `<div class="statCard"><div class="statVal">${value}</div><div class="statLabel">${label}</div>${sub ? `<div class="statSub">${sub}</div>` : ""}</div>`;

function analysisCardsHtml(a) {
  const consideration = a.integrity.consideration;
  const integrity = a.integrity.panel;
  const kappa = a.agreement.collapsed.fleiss_kappa;
  const instances = Object.values(a.behaviour_inventory.by_behavior).reduce((sum, count) => sum + count, 0);
  return [
    statCard(instances, "behaviour instances", `across ${a.source.panel.length} member${a.source.panel.length === 1 ? "" : "s"}`),
    consideration.records_present
      ? statCard(pct(consideration.considered_fraction), "consideration coverage",
          consideration.missing_units ? `${consideration.missing_units} player-turns missed` : "every living player, every turn")
      : statCard("n/a", "consideration coverage", "predates the per-turn sweep"),
    consideration.records_present
      ? statCard(pct(consideration.no_behaviour_rate), "no-behaviour rate", "considered units marked as no-ops")
      : "",
    statCard(integrity.broken_citations, "broken citations",
      `of ${integrity.total_citations} (${pct(integrity.broken_rate)})`),
    statCard(kappa == null ? "—" : kappa.toFixed(2), "Fleiss κ",
      `agreement, collapsed to ${escapeHtml(a.agreement.collapsed.grouping)} · turn ±${a.agreement.collapsed.turn_window}`),
  ].join("");
}

/* the member × turn forced-consideration grid, straight from the run's eval_coverage records */
function considerationGridHtml() {
  const records = EVAL.consideration || [];
  const header = '<div class="eyebrow" style="margin:18px 0 6px">consideration coverage — who the panel accounted for, per turn</div>';
  if (!records.length)
    return header + '<div class="empty">This run predates the per-turn sweep, so there are no consideration records.</div>';
  const gridTurns = [...new Set(records.map(r => r.turn))].sort((a, b) => a - b);
  const memberIds = [...new Set(records.map(r => r.evaluator_index))].sort((a, b) => a - b);
  const byMemberTurn = new Map(records.map(r => [`${r.evaluator_index}|${r.turn}`, r]));
  const rows = memberIds.map(idx => {
    const cells = gridTurns.map(turn => {
      const r = byMemberTurn.get(`${idx}|${turn}`);
      const cls = !r ? "none" : r.missing.length ? "miss" : r.tagged.length ? "tag" : "noop";
      const tip = !r ? `turn ${turn} — no record (the call failed before reporting)` :
        `turn ${turn} — tagged: ${r.tagged.join(", ") || "—"} · no behaviour: ${r.no_behaviour.join(", ") || "—"} · missing: ${r.missing.join(", ") || "—"}`;
      return `<span class="covCell ${cls}" title="${escapeHtml(tip)}"></span>`;
    }).join("");
    return `<div class="covRow"><span class="covLabel mono" style="color:${annotatorColor(idx)}">${escapeHtml(annotatorLabel(idx))}</span><span class="covCells">${cells}</span></div>`;
  }).join("");
  const legend = `<div class="covLegend mono">turns ${gridTurns[0]}–${gridTurns.at(-1)} →
    &nbsp;<span class="covCell tag"></span> tagged someone
    &nbsp;<span class="covCell noop"></span> all no-behaviour
    &nbsp;<span class="covCell miss"></span> someone missed
    &nbsp;<span class="covCell none"></span> no record</div>`;
  return header + rows + legend;
}

function distributionBarsHtml(distribution, panelCount) {
  const counts = Array.from({ length: panelCount }, (_, i) => distribution[String(i + 1)] ?? 0);
  const max = Math.max(1, ...counts);
  return counts.map((count, i) =>
    `<div class="miniBarRow"><span class="mono miniBarLabel">${i + 1}/${panelCount}</span>
      <span class="miniBar"><span style="width:${Math.round(count / max * 100)}%"></span></span>
      <span class="mono miniBarValue">${count}</span></div>`).join("");
}

function agreementHtml(a) {
  const panelCount = a.source.panel.length;
  if (panelCount < 2) return "";
  return `<div class="eyebrow" style="margin:18px 0 6px">cross-member agreement — how many members flagged each item</div>
    <div class="analysisCols">
      <div><div class="statNote" style="margin:0 0 4px">strict — exact (player, behavior, turn)</div>
        ${distributionBarsHtml(a.agreement.strict.distribution, panelCount)}</div>
      <div><div class="statNote" style="margin:0 0 4px">collapsed — same ${escapeHtml(a.agreement.collapsed.grouping)}, turn ±${a.agreement.collapsed.turn_window}</div>
        ${distributionBarsHtml(a.agreement.collapsed.distribution, panelCount)}</div>
    </div>`;
}

function calibrationHtml(a) {
  const rows = a.calibration.by_member.map(m =>
    `<tr><td class="mono" style="color:${annotatorColor(m.evaluator_index)};font-weight:600">${escapeHtml(annotatorLabel(m.evaluator_index))}</td>
      <td class="mono" style="font-size:11px">${m.tag_count}</td>
      <td class="mono" style="font-size:11px">${m.mean_confidence.toFixed(2)}</td>
      <td class="mono" style="font-size:11px">${m.citation_count}</td>
      <td class="mono" style="font-size:11px">${pct(m.quote_mismatch_rate)}</td></tr>`).join("");
  return `<div class="eyebrow" style="margin:18px 0 0">calibration — each member's volume, confidence, and quoting discipline</div>
    <table class="statsTable"><thead><tr><th>member</th><th>tags</th><th>mean confidence</th><th>citations</th><th>quote mismatches</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

/* the inventory bars re-aggregate client-side at the selected grains (the analysis artifact keeps
   the canonical default-grain numbers); bars are the grouping keys at the code grain, coloured by
   valence where the key still has a single one, and honor the agreement filter like the table */
function inventoryHtml(a) {
  const { codeGrain, locusGrain } = evaluationPanel;
  const totals = {};
  panelFilteredTableTags(evaluationPanel).forEach(tags =>
    Object.entries(tags).forEach(([code, info]) => totals[code] = (totals[code] ?? 0) + info.count));
  const byCode = Object.entries(totals).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
  if (!byCode.length) return "";
  const max = Math.max(...byCode.map(([, count]) => count));
  const rows = byCode.map(([code, count]) => {
    const [vc, vt] = valenceStyleAtGrain(code, codeGrain);
    return `<div class="miniBarRow"><span class="tagchip miniBarLabel" style="color:${vc};background:${vt}">${code}</span>
      <span class="miniBar"><span style="width:${Math.round(count / max * 100)}%"></span></span>
      <span class="mono miniBarValue">${count}</span></div>`;
  }).join("");
  const absent = codeGrain === "behavior" && locusGrain === "turn" ? a.behaviour_inventory.groups_absent : [];
  const absentNote = absent.length ? `<div class="statNote">no ${absent.join(" or ")} behaviours were observed.</div>` : "";
  const locusLabel = locusGrain === "turn" ? "turn" : locusGrain === "game" ? "whole game" : "overlapping cited spans";
  return `<div class="eyebrow" style="margin:18px 0 6px">behaviour inventory — distinct (player, ${codeGrain}, ${locusLabel}) instances</div>${rows}${absentNote}`;
}

/* sibling leniency: does a member tag players built on its own provider differently? */
function leniencyHtml(a) {
  const pairs = a.sibling_leniency.pairs;
  if (!pairs.length || !pairs.some(p => p.is_sibling)) return "";
  const memberIds = [...new Set(pairs.map(p => p.evaluator_index))].sort((x, y) => x - y);
  const players = GAME.players.map(p => p.name);
  const byKey = new Map(pairs.map(p => [`${p.evaluator_index}|${p.player}`, p]));
  const head = players.map(name => `<th style="color:${colorOf(name)}">${tagOf(name)}</th>`).join("");
  const rows = memberIds.map(idx => {
    const cells = players.map(name => {
      const p = byKey.get(`${idx}|${name}`);
      if (!p) return "<td>—</td>";
      return `<td class="mono${p.is_sibling ? " sibling" : ""}" style="font-size:11px" title="${p.is_sibling ? "same provider as this member" : ""}">
        +${p.positive_tags} / −${p.negative_tags}</td>`;
    }).join("");
    return `<tr><td class="mono" style="color:${annotatorColor(idx)};font-weight:600">${escapeHtml(annotatorLabel(idx))}</td>${cells}</tr>`;
  }).join("");
  return `<div class="eyebrow" style="margin:18px 0 0">sibling leniency — positive / negative tags per player (outlined = same provider as the member)</div>
    <table class="statsTable"><thead><tr><th>member</th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

function divergencesHtml(a) {
  if (!a.divergences.length) return "";
  const layerBlock = (label, text) => text
    ? `<div class="divLayer"><span class="role">${label}</span><pre>${escapeHtml(text)}</pre></div>` : "";
  const items = a.divergences.map(d =>
    `<details class="divergence"><summary><span class="mono meta">turn ${d.turn}</span>
        <span style="color:${colorOf(d.player)};font-weight:600">${tagOf(d.player)}</span>
        <span class="tagchip" style="color:${valenceStyle(d.behavior)[0]};background:${valenceStyle(d.behavior)[1]}">${d.behavior}</span>
        <span class="mono meta">flagged by ${escapeHtml(annotatorLabel(d.evaluator_index))}</span></summary>
      ${layerBlock("thought", d.layers.thought)}${layerBlock("message", d.layers.message)}
      ${layerBlock("action", d.layers.action)}${layerBlock("note", d.layers.note)}</details>`).join("");
  return `<div class="eyebrow" style="margin:18px 0 6px">divergences — in-the-moment gap tags with the player's four layers that turn</div>${items}`;
}
