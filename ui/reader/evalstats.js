/* Each evaluator is a separate observation; counts never pool panel votes. */
const EVAL_OUTCOMES = [
  ["fulfilled", "Fulfilled", "coop"], ["violated", "Violated", "adversarial"],
  ["open", "Open", "open"], ["released", "Released", "neutral"],
  ["not_triggered", "Not triggered", "gold"], ["unassessable", "Unassessable", "neutral"]
];
function evalAction(index, kind, status = "", code = "", basis = "") {
  return 'openEvalResearch(' + [index,kind,status,code,basis].map(v => JSON.stringify(v)).join(',') + ')';
}
function evalKpi(value, label, detail, action = "") {
  const tag = action ? "button" : "div";
  return '<' + tag + ' class="evalKpi"' + (action ? ' onclick="' + escapeHtml(action) + '"' : '') + '>' +
    '<strong>' + value + '</strong><span>' + label + '</span><small>' + detail + '</small></' + tag + '>';
}
function renderEvalStats() {
  const sheet = el("evalSheet");
  if (!EVAL.has_evaluation) { sheet.innerHTML = '<p class="empty">No evaluation yet.</p>'; return; }
  const summaries = EVAL.analysis?.members || (EVAL.members || []).map(summarizeMember);
  sheet.innerHTML = '<p class="evalIntro">Each evaluator is shown separately. Select an outcome or claim code to inspect its records.</p>' +
    summaries.map(m => {
      const index = m.index;
      const total = Object.values(m.statuses || {}).reduce((a,b) => a + b, 0);
      const denominator = m.adherence?.resolved_agent_episodes || 0;
      const fulfilled = m.adherence?.fulfilled || 0;
      const coverage = m.coverage || {};
      const screened = (coverage.candidate || 0) + (coverage.no_candidate || 0) + (coverage.unclear || 0);
      const claims = m.claims_by_basis || {};
      const outcomeBar = total ? EVAL_OUTCOMES.filter(([key]) => m.statuses[key]).map(([key,label,tone]) =>
        '<button class="evalBarSeg tone-' + tone + '" style="flex:' + m.statuses[key] +
        '" title="' + m.statuses[key] + ' ' + label.toLowerCase() + ' commitments" aria-label="Show ' +
        m.statuses[key] + ' ' + label.toLowerCase() + ' commitments" onclick="' +
        escapeHtml(evalAction(index,"episode",key)) + '"></button>').join('') : '<span class="evalBarEmpty">No valid commitments</span>';
      const outcomeKey = EVAL_OUTCOMES.filter(([key]) => m.statuses[key]).map(([key,label,tone]) =>
        '<button class="evalLegendItem" onclick="' + escapeHtml(evalAction(index,"episode",key)) + '">' +
        '<i class="tone-' + tone + '"></i><span>' + label + '</span><strong>' + m.statuses[key] + '</strong></button>').join('');
      const claimRows = Object.entries(claims).filter(([,v]) => v.population).map(([raw,v]) => {
        const basis = raw === "null" ? "unknown" : raw;
        const codes = Object.entries(v.codes || {}).sort((a,b) => b[1]-a[1]);
        return '<div class="evalBasis"><button class="evalBasisHead" onclick="' + escapeHtml(evalAction(index,"claim","","",basis)) +
          '"><strong>' + escapeHtml(pretty(basis === "unknown" ? "unknown basis" : basis)) + '</strong><span>' + v.population +
          ' claims ↗</span></button><div class="evalCodeList">' + (codes.length ? codes.map(([code,n]) =>
          '<button class="evalCode" onclick="' + escapeHtml(evalAction(index,"claim","",code,basis)) + '"><span>' +
          escapeHtml(pretty(code)) + '</span><strong>' + n + '</strong></button>').join('') : '<span class="evalNoCodes">No codes</span>') +
          '</div></div>';
      }).join('');
      return '<article class="evalDashboard"><header class="evalDashHead"><div><span class="evalEyebrow">Evaluator ' +
        (index + 1) + '</span><h3>' + escapeHtml(m.label) + '</h3></div><span class="evalRunStatus">' +
        escapeHtml(pretty(m.status)) + '</span></header>' +
        '<div class="evalKpis">' +
        evalKpi(screened + '/' + m.message_count,'messages screened','Includes ordinary messages') +
        evalKpi(m.episode_count ?? total,'commitments','Valid assessments',evalAction(index,"episode")) +
        evalKpi(m.claim_count ?? Object.values(m.claim_statuses || {}).reduce((a,b) => a+b,0),'claims','Valid assessments',evalAction(index,"claim")) +
        evalKpi(m.invalid_units,'need review','Excluded from counts') + '</div>' +
        '<div class="evalDashGrid"><section class="evalSection"><div class="evalSectionHead"><h4>Commitment outcomes</h4><span>' + total + ' valid</span></div>' +
        '<div class="evalOutcomeBar" role="group" aria-label="Commitment outcomes">' + outcomeBar + '</div><div class="evalLegend">' + outcomeKey + '</div>' +
        '<div class="evalAdherence"><span>Adherence among resolved, applicable agent commitments</span><strong>' +
        (denominator ? fulfilled + ' / ' + denominator + ' · ' + Math.round(fulfilled / denominator * 100) + '%' : 'Unavailable') +
        '</strong></div><p class="evalFine">Human actions and unresolved commitments are excluded from adherence.</p></section>' +
        '<section class="evalSection"><div class="evalSectionHead"><h4>Claims by evidence basis</h4><span>' +
        (m.claim_count ?? '') + ' valid</span></div>' + (claimRows || '<p class="empty">No valid claims.</p>') +
        '<p class="evalFine">A claim can carry more than one code; code totals need not equal the claim count.</p></section></div>' +
        '<details class="evalDetails"><summary>Screening, provenance and citation checks</summary><div class="evalDetailGrid">' +
        '<p><strong>Message screening</strong><br>' + readableCounts(coverage) + '</p>' +
        '<p><strong>Claim assessments</strong><br>' + readableCounts(m.claim_statuses || {}) + '</p>' +
        '<p><strong>Action sources</strong><br>' + readableCounts(m.provenance || {}) + '</p>' +
        '<p><strong>Citations</strong><br>' + (m.integrity?.total_citations || 0) + ' total · ' +
        (m.integrity?.broken_citations || 0) + ' broken · ' + (m.integrity?.quote_mismatches || 0) +
        ' quote mismatches</p></div></details></article>';
    }).join('') +
    '<p class="evalFootnote">Forks share inherited history and are related continuations. Citation checks do not validate interpretation.</p>';
}
function openEvalResearch(index, kind, status = "", code = "", basis = "") {
  closeStats();
  researchVisibilityChosen = true;
  Object.assign(state.filters, {actor:"", kind, status, code, basis, validOnly:true, showCommentary:true});
  state.filters.annotators = new Set([index]);
  state.mode = "research";
  renderFilters(); renderModeToggle(); renderSynthesis(); renderTimeline();
  el("timelinePane")?.scrollIntoView?.({behavior:"smooth", block:"start"});
}
function readableCounts(counts) {
  return Object.entries(counts).map(([key,n]) => escapeHtml(pretty(key)) + ': ' + n).join(' · ') || 'None';
}
function summarizeMember(m) {
  const episodes = (m.episodes || []).filter(e => e.valid), claims = (m.claims || []).filter(c => c.valid);
  const countBy = (rows,key) => rows.reduce((out,r) => (out[r[key]]=(out[r[key]]||0)+1,out),{});
  const eligible = episodes.filter(e => ["fulfilled","violated"].includes(e.status) && e.action_provenance === "agent_choice");
  const fulfilled = eligible.filter(e => e.status === "fulfilled").length, cites = unitCitations(m);
  return {index:m.index,label:m.label,status:m.status,message_count:(m.coverage || []).length,
    coverage:countBy(m.coverage || [],"status"),episode_count:episodes.length,claim_count:claims.length,
    invalid_units:[...(m.agreements || []),...(m.episodes || []),...(m.claims || [])].filter(u => !u.valid).length,
    statuses:Object.fromEntries(["open","fulfilled","violated","released","not_triggered","unassessable"].map(s => [s,episodes.filter(e => e.status === s).length])),
    provenance:countBy(episodes,"action_provenance"), claim_statuses:countBy(claims,"status"),
    adherence:{fulfilled,resolved_agent_episodes:eligible.length,proportion:eligible.length ? fulfilled/eligible.length : null},
    claims_by_basis:Object.fromEntries(["stated_plan","recorded_fact",null].map(b => [b,{
      population:claims.filter(c => c.basis === b).length,
      codes:claims.filter(c => c.basis === b).flatMap(c => c.codes || []).reduce((o,c) => (o[c]=(o[c]||0)+1,o),{})
    }])),
    integrity:{total_citations:cites.length,broken_citations:cites.filter(e => !e.resolved).length,
      quote_mismatches:cites.filter(e => e.quote_matched === false).length}};
}
