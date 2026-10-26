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
  if (isTwoTagSchema() && EVAL.has_evaluation && EVAL.research_report) {
    el("evalSheet").innerHTML = renderResearchGameReport(EVAL.research_report, GAME);
    return;
  }
  if (isTwoTagSchema()) return renderEvalStatsV3();
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
function renderEvalStatsV3() {
  const sheet = el("evalSheet");
  if (!EVAL.has_evaluation) { sheet.innerHTML = '<p class="empty">No evaluation yet.</p>'; return; }
  const summaries = EVAL.analysis?.members || (EVAL.members || []).map(summarizeMemberV3);
  const statuses = [["proposed","Proposed","open"],["agreed","Agreed","open"],["committed","Committed","open"],["made","Made","open"],["fulfilled","Fulfilled","coop"],
    ["violated","Violated","adversarial"],["unresolved","Unresolved","neutral"],["closed","Closed","neutral"]];
  sheet.innerHTML = '<p class="evalIntro">Shared agreements appear as one tag; each participant obligation is assessed separately. Broken citations are excluded.</p>' +
    summaries.map(m => {
      const index = m.index, cs = m.commitment_statuses || {}, ss = m.statement_statuses || {};
      const denominator = m.adherence?.resolved_agent_commitments || 0;
      return '<article class="evalDashboard"><header class="evalDashHead"><div><span class="evalEyebrow">Evaluator ' + (index + 1) +
        '</span><h3>' + escapeHtml(m.label) + '</h3></div><span class="evalRunStatus">' + escapeHtml(pretty(m.status)) + '</span></header>' +
        '<div class="evalKpis">' + evalKpi(m.commitment_count,'commitments',m.established_count + ' established · ' + m.proposal_count + ' proposals',evalAction(index,'commitment')) +
        evalKpi(m.statement_count,'statements','One status per assessment',evalAction(index,'statement')) +
        evalKpi(m.invalid_units,'need review','Excluded from counts') + '</div>' +
        '<div class="evalDashGrid"><section class="evalSection"><div class="evalSectionHead"><h4>Commitments</h4><span>' +
        (m.obligation_count ?? m.commitment_count) + ' participant obligations</span></div><div class="evalLegend">' + statuses.filter(([key]) => cs[key]).map(([key,label,tone]) =>
        '<button class="evalLegendItem" onclick="' + escapeHtml(evalAction(index,'commitment',key)) + '"><i class="tone-' + tone +
        '"></i><span>' + label + '</span><strong>' + cs[key] + '</strong></button>').join('') + '</div>' +
        '<div class="evalAdherence"><span>Adherence among established, resolved agent commitments</span><strong>' +
        (denominator ? m.adherence.fulfilled + ' / ' + denominator + ' · ' + Math.round(m.adherence.fulfilled / denominator * 100) + '%' : 'Unavailable') +
        '</strong></div><p class="evalFine">Closed reasons: ' + readableCounts(m.closure_reasons || {}) + '</p></section>' +
        '<section class="evalSection"><div class="evalSectionHead"><h4>Statements by evidence basis</h4><span>' +
        m.statement_count + ' valid</span></div>' + Object.entries(m.statements_by_basis || {}).map(([basis,v]) =>
        '<div class="evalBasis"><button class="evalBasisHead" onclick="' + escapeHtml(evalAction(index,'statement','','',basis)) +
        '"><strong>' + escapeHtml(pretty(basis)) + '</strong><span>' + v.population + ' statements ↗</span></button><div class="evalCodeList">' +
        Object.entries(v.statuses || {}).map(([status,n]) => '<button class="evalCode" onclick="' + escapeHtml(evalAction(index,'statement',status,'',basis)) +
        '"><span>' + escapeHtml(pretty(status)) + '</span><strong>' + n + '</strong></button>').join('') + '</div></div>').join('') +
        '<p class="evalFine">Deceptive includes contradiction and counts once. ' + (m.multi_basis_propositions || 0) +
        ' propositions assessed on both bases.</p></section></div>' +
        '<details class="evalDetails"><summary>Coverage and citation checks</summary><p>' + readableCounts(m.coverage || {}) +
        ' · ' + (m.integrity?.broken_citations || 0) + ' broken citations</p></details></article>';
    }).join('');
}
function summarizeMemberV3(m) {
  const commitments = (m.commitments || []).filter(u => u.valid), statements = (m.statements || []).filter(u => u.valid);
  const groups = new Map(); commitments.forEach(c => { const key = c.agreement_id ? 'agreement:' + c.agreement_id : 'unit:' + c.id; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(c); });
  const countBy = (rows,key) => rows.reduce((o,r) => (o[r[key]]=(o[r[key]]||0)+1,o),{});
  const eligible = commitments.filter(c => c.established && ["fulfilled","violated"].includes(c.status) && c.action_provenance === 'agent_choice');
  const bases = Object.fromEntries(['stated_plan','recorded_fact'].map(b => [b,{population:statements.filter(s => s.basis === b).length,
    statuses:countBy(statements.filter(s => s.basis === b),'status')}]));
  const keys = new Map(); statements.forEach(s => { const k = [s.actor,s.passage.span,s.proposition,s.relevant_time].join(':');
    if (!keys.has(k)) keys.set(k,new Set()); keys.get(k).add(s.basis); });
  return {index:m.index,label:m.label,status:m.status,commitment_count:groups.size,obligation_count:commitments.length,statement_count:statements.length,
    proposal_count:[...groups.values()].filter(g => !g.some(c => c.established)).length,established_count:[...groups.values()].filter(g => g.some(c => c.established)).length,
    invalid_units:[...(m.commitments || []),...(m.statements || [])].filter(u => !u.valid).length,
    commitment_statuses:countBy(commitments,'status'),statement_statuses:countBy(statements,'status'),statements_by_basis:bases,
    multi_basis_propositions:[...keys.values()].filter(s => s.size > 1).length,
    closure_reasons:countBy(commitments.filter(c => c.status === 'closed'),'reason'),coverage:countBy(m.coverage || [],'status'),
    adherence:{fulfilled:eligible.filter(c => c.status === 'fulfilled').length,resolved_agent_commitments:eligible.length},
    integrity:{broken_citations:unitCitations(m).filter(c => !c.resolved).length}};
}
function openEvalResearch(index, kind, status = "", code = "", basis = "") {
  closeStats();
  if (reviewBlind) { toast("Research annotations stay hidden while you code messages blind."); return; }
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
