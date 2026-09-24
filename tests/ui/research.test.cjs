const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function reader({human = false, finished = false, evaluate = false} = {}) {
  const nodes = new Map();
  const context = vm.createContext({document: {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {querySelector: () => ({})});
    return nodes.get(id);
  }}, window: {BOOT: {
    game: {players:[{name:'alice'}], human_players:human ? ['alice'] : [], continents:[],
      steps:[{board:{alaska:[0,2]},turn:1}],result:finished ? {winner:'alice'} : null},
    evaluation: {spans:[{span:0,turn:1,step:0,layer:'message',player:'alice',text:'A <promise>.'}]},
    urls:{evaluate:evaluate ? '/api/evaluate' : ''}, live:false, live_running:false,
  }}});
  for (const file of ['state','timeline','evalstats','drawer','evaluate']) {
    vm.runInContext(readFileSync('ui/reader/' + file + '.js','utf8'),context);
  }
  return code => vm.runInContext(code,context);
}

test('research annotations start hidden during a human game and can be shown explicitly', () => {
  const run = reader({human:true});
  assert.equal(run('state.filters.showCommentary'), false);
  run('EVAL.has_evaluation=true; EVAL.members=[]; renderSynthesis();');
  assert.equal(run('el("synthSection").hidden'), true);
  assert.match(run('comparisonHtml()'), /Enable annotations/);
  run('renderTimeline = () => {}; el("commToggle").onclick()');
  assert.equal(run('state.filters.showCommentary'), true);
  assert.equal(run('researchVisibilityChosen'), true);
  assert.equal(run('el("synthSection").hidden'), false);
  assert.equal(reader({human:true, finished:true})('state.filters.showCommentary'), true);
});

test('saved turn commentary follows the human visibility toggle', () => {
  const run = reader({human:true});
  run(`EVAL.coverage={source_matches:true}; EVAL.annotators=[{index:0,label:"Coder"}];
    EVAL.live_commentary=[{turn:1,evaluator_index:0,text:"No <commitment> this turn."}];
    EVAL.live_final=[{turn:1,evaluator_index:0,text:"Game <ended>; outcome reviewed."}];
    state.filters.annotators.add(0);`);
  assert.doesNotMatch(run('turnCard(1)'), /No &lt;commitment&gt;/);
  assert.doesNotMatch(run('turnCard(1)'), /Game &lt;ended&gt;/);
  run('state.filters.showCommentary=true');
  assert.match(run('turnCard(1)'), /No &lt;commitment&gt; this turn/);
  assert.match(run('turnCard(1)'), /final review.*Game &lt;ended&gt;; outcome reviewed/s);
});

test('a finished game offers to complete an interrupted final review', () => {
  const run = reader({finished:true, evaluate:true});
  run(`EVAL.has_evaluation=true; EVAL.live_evaluation=true; EVAL.live_finalized=false;
    EVAL.versions=[{run_id:"20260923-120000-1",created_at:"2026-09-23T12:00:00Z",is_current:true,panel:["Coder"]}];
    renderEvalBar();`);
  assert.match(run('el("evalBar").innerHTML'), /Finish live review/);
  assert.match(run('el("evalBar").innerHTML'), /follow completed turns/);
});

test('quoted source is escaped and highlighted without turning evidence into markup', () => {
  const run = reader();
  const html = run('evidenceText({text:"A <promise>."},{quote:"<promise>",quote_matched:true},false)');
  assert.equal(html,'A <mark>&lt;promise&gt;</mark>.');
  assert.equal(run('evidenceText({text:"A <promise>."},{quote:"invented",quote_matched:false},false)'),
    'A &lt;promise&gt;.');
});

test('initial state stays in the raw trace without cluttering gameplay or losing fork boundaries', () => {
  const run = reader();
  run(`EVAL.spans=[
    {span:0,turn:0,step:0,layer:"state",text:"Initial board and rules",cursor:{before:0,after:1}},
    {span:1,turn:0,step:1,layer:"action",text:"alice places an army",cursor:{before:1,after:2}}
  ]; rebuildTrace();
  function forkSeparator(cursor) { return '<button>fork ' + cursor + '</button>'; }`);
  const timeline = run('expandedBody(0)');
  assert.doesNotMatch(timeline,/Initial board and rules/);
  assert.match(timeline.replace(/<[^>]*>/g,''),/alice places an army/);
  assert.match(timeline,/fork 0/);
  assert.match(timeline,/fork 2/);
  run('state.drawer={focus:null,cites:new Set()}; renderPanels();');
  const drawer = run('el("panelRoot").innerHTML');
  assert.match(drawer,/Raw trace/);
  assert.match(drawer,/Initial board and rules/);
  assert.match(drawer,/drawer-span-0/);
  assert.match(drawer.replace(/<[^>]*>/g,''),/alice places an army/);
});

test('reader distinguishes missing screening from no candidate even with no extracted units', () => {
  const run = reader();
  run(`EVAL.members=[{index:0,label:"Coder",agreements:[],episodes:[],claims:[],
    coverage:[{span:0,status:"missing",explanation:"API failed"},
              {span:1,status:"no_candidate",explanation:"Ordinary greeting"}]}];
    state.filters.annotators.add(0);`);
  const html = run('researchHtml()');
  assert.match(html,/missing/);
  assert.match(html,/no candidate/);
  assert.match(html,/openLineDrawer\(0\)/);
  assert.match(html,/No research units/);
});

test('research summaries exclude human interventions and unresolved or invalid obligations', () => {
  const run = reader();
  run(`EVAL.members=[{index:0,label:"Coder",status:"partial",agreements:[],claims:[],coverage:[],
    episodes:[{id:"e1",valid:true,status:"fulfilled",action_provenance:"agent_choice"},
              {id:"e2",valid:true,status:"violated",action_provenance:"human"},
              {id:"e3",valid:true,status:"open",action_provenance:"agent_choice"},
              {id:"e4",valid:false,status:"violated",action_provenance:"agent_choice"}]}];`);
  const result = JSON.parse(run('JSON.stringify(summarizeMember(EVAL.members[0]))'));
  assert.deepEqual(result.adherence,{fulfilled:1,resolved_agent_episodes:1,proportion:1});
  assert.equal(result.statuses.open,1);
  assert.equal(result.invalid_units,1);
});

test('evaluation dashboard keeps denominators visible and drills into valid records for one evaluator', () => {
  const run = reader();
  run(`EVAL.has_evaluation=true; EVAL.members=[{index:0,label:"Coder",status:"partial",coverage:[{status:"candidate"}],
    agreements:[],claims:[{valid:true,basis:"recorded_fact",status:"assessable",codes:["message_contradicted"]}],
    episodes:[{valid:true,status:"fulfilled",action_provenance:"agent_choice"},
              {valid:true,status:"unassessable",action_provenance:"unknown"},
              {valid:false,status:"violated",action_provenance:"agent_choice"}]}];
    EVAL.annotators=[{index:0,label:"Coder"}];
    function closeStats(){} function renderFilters(){} function renderModeToggle(){} function renderTimeline(){};
    renderEvalStats();`);
  const html = run('el("evalSheet").innerHTML');
  assert.match(html,/1\/1.*messages screened/);
  assert.match(html,/2.*commitments.*1.*claims.*1.*need review/s);
  assert.match(html,/1 \/ 1 · 100%/);
  assert.match(html,/message contradicted/);
  run('openEvalResearch(0,"claim","","message_contradicted","recorded_fact")');
  assert.equal(run('state.filters.kind'), 'claim');
  assert.equal(run('state.filters.validOnly'), true);
  assert.equal(run('state.filters.code'), 'message_contradicted');
  assert.equal(run('state.filters.basis'), 'recorded_fact');
  assert.equal(run('state.mode'), 'research');
  assert.equal(run('unitVisible({annotator:0,kind:"claim",valid:true,codes:["message_contradicted"],basis:"recorded_fact"})'), true);
  assert.equal(run('unitVisible({annotator:0,kind:"claim",valid:false,codes:["message_contradicted"],basis:"recorded_fact"})'), false);
  run('state.filters.code=""; state.filters.basis="unknown";');
  assert.equal(run('unitVisible({annotator:0,kind:"claim",valid:true,codes:[],basis:null})'), true);
  assert.equal(run('unitVisible({annotator:0,kind:"claim",valid:true,codes:[],basis:"recorded_fact"})'), false);
});

test('proposal summaries use readable terms and responses instead of internal identifiers', () => {
  const run = reader();
  run(`var proposal={kind:"agreement",id:"A_NA1",initiator:"alice",annotator:0,valid:true,
    proposals:[{author:"alice",terms:"Mutual nonattack in North America",evidence:[],
    responses:[{player:"bob",status:"rejected",evidence:[]}]}]};`);
  const html = run('unitCard(proposal)');
  const summary = html.match(/<summary>(.*?)<\/summary>/s)[1];
  assert.match(summary,/Mutual nonattack in North America/);
  assert.match(summary,/Rejected/);
  assert.doesNotMatch(summary,/A_NA1|Shared agreement/);
  assert.match(html,/Internal reference: A_NA1/);
  assert.doesNotMatch(html.split('<summary>')[0],/ open[ >]/);
  assert.match(run('unitCard(proposal,true)').split('<summary>')[0],/ open /);
});

test('validation notices are grouped without presenting them as API failures', () => {
  const run = reader();
  const html = run(`evaluationIssuesHtml([
    {stage:"extraction",explanation:"Unexpected or repeated message span 288"},
    {stage:"extraction",explanation:"Unexpected or repeated message span 288"}
  ])`);
  assert.equal((html.match(/<details/g) || []).length,1);
  assert.equal((html.match(/Unexpected or repeated/g) || []).length,1);
  assert.match(html,/× 2/);
  assert.doesNotMatch(html,/extraction failed/);
});

test('outcome colors distinguish commitments and claim evidence without endorsing invalid units', () => {
  const run = reader();
  assert.equal(run('unitTone({valid:true,status:"violated"})'),'adversarial');
  assert.equal(run('unitTone({valid:true,status:"fulfilled"})'),'coop');
  assert.equal(run('unitTone({valid:true,status:"open"})'),'open');
  assert.equal(run('unitTone({valid:true,codes:["message_contradicted","deceptive_communication"]})'),'deceptive');
  assert.equal(run('unitTone({valid:false,status:"fulfilled"})'),'neutral');
});
