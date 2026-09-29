const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function reader({human = false, finished = false, evaluate = false} = {}) {
  const nodes = new Map();
  const context = vm.createContext({requestAnimationFrame: () => {}, document: {getElementById(id) {
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

test('completed evaluator calls appear as chronological timeline entries', () => {
  const run = reader({human:true});
  run(`EVAL.coverage={source_matches:true}; EVAL.annotators=[{index:0,label:"Coder"}];
    EVAL.live_commentary=[{turn:1,round:1,evaluator_index:0,text:"No <commitment> this round."}];
    EVAL.live_final=[{turn:1,evaluator_index:0,text:"Game <ended>; outcome reviewed."}];
    state.filters.annotators.add(0);`);
  assert.doesNotMatch(run('turnCard(1)'), /No &lt;commitment&gt;/);
  assert.doesNotMatch(run('reviewTimelineEvents(1)'), /No &lt;commitment&gt;/);
  run('state.filters.showCommentary=true');
  const timeline = run('turnCard(1) + reviewTimelineEvents(1)');
  assert.match(timeline, /<\/article><article class="researchReviewEvent"/);
  assert.match(timeline, /Research review · Round 1 · Coder.*No &lt;commitment&gt; this round/s);
  assert.match(timeline, /Final research review · Coder.*Game &lt;ended&gt;; outcome reviewed/s);
  const template = readFileSync('ui/reader/reader.html.j2','utf8');
  assert.doesNotMatch(template, /readerAnalysis|<summary>Game analysis<\/summary>/);
  assert.ok(template.indexOf('id="timeline"') < template.indexOf('id="evalBar"'));
});

test('a live review has no version or re-run panel beneath the timeline', () => {
  const run = reader({finished:true, evaluate:true});
  run(`EVAL.has_evaluation=true; EVAL.live_evaluation=true; EVAL.live_finalized=false;
    EVAL.versions=[{run_id:"20260923-120000-1",created_at:"2026-09-23T12:00:00Z",is_current:true,panel:["Coder"]}];
    renderEvalCta(); renderEvalBar(); renderSynthesis();`);
  assert.equal(run('el("evalCta").hidden'),true);
  assert.equal(run('el("evalBar").hidden'),true);
  assert.equal(run('el("synthSection").hidden'),true);
  assert.doesNotMatch(run('el("evalBar").innerHTML'), /versionSelect|Re-run panel/);
});

test('active calls appear between the relevant turns and disappear when idle', () => {
  const run = reader({evaluate:true});
  run(`show=()=>{}; EVAL.live_evaluation=true; EVAL.running=true;
    EVAL.spans=[{span:0,turn:1,round:1,step:0,layer:"message",player:"alice",text:"First."},
      {span:1,turn:2,round:2,step:0,layer:"message",player:"alice",text:"Second."}];
    rebuildTrace(); roundByTurn={1:1,2:2}; state.filters.showCommentary=true;
    EVAL.review_active=true; EVAL.reviewing_round=1; renderTimeline()`);
  let html = run('el("timeline").innerHTML');
  assert.ok(html.indexOf('data-turn="1"') < html.indexOf('◐ Research review · Round 1 in progress'));
  assert.ok(html.indexOf('◐ Research review · Round 1 in progress') < html.indexOf('data-turn="2"'));
  assert.equal((html.match(/activeReviewEvent/g) || []).length,1);

  run('EVAL.review_active=false; renderTimeline()');
  assert.doesNotMatch(run('el("timeline").innerHTML'), /activeReviewEvent|no completed rounds yet/);

  run('EVAL.review_active=true; EVAL.reviewing_final=true; renderTimeline()');
  html = run('el("timeline").innerHTML');
  assert.ok(html.indexOf('data-turn="2"') < html.indexOf('◐ Final research review in progress'));
  assert.equal((html.match(/activeReviewEvent/g) || []).length,1);

  run(`GAME.result={winner:"alice"}; EVAL.running=false; EVAL.review_active=false;
    EVAL.error="API unavailable"; EVAL.live_finalized=false; EVAL.live_panel=["test:coder"]; renderTimeline()`);
  assert.match(run('el("timeline").innerHTML'), /Final research review paused.*Resume review/s);
});

test('a paused final review resumes with its configured live panel', async () => {
  const run = reader({finished:true, evaluate:true});
  run(`EVAL.live_evaluation=true; EVAL.live_panel=["test:coder"]; EVAL.error="API unavailable";
    fetch=async (url, options) => {globalThis.sent={url,body:JSON.parse(options.body)}; return {ok:true};};
    renderTimeline=()=>{}; pollEvaluation=()=>{globalThis.polling=true};`);

  await run('resumeLiveReview()');

  assert.equal(run('sent.url'),'/api/evaluate');
  assert.equal(JSON.stringify(run('sent.body')),JSON.stringify({evaluators:['test:coder'],live:true}));
  assert.equal(run('EVAL.error'),null);
  assert.equal(run('EVAL.running'),true);
  assert.equal(run('polling'),true);
});

test('quoted source is escaped and highlighted without turning evidence into markup', () => {
  const run = reader();
  const html = run('evidenceText({text:"A <promise>."},{quote:"<promise>",quote_matched:true},false)');
  assert.equal(html,'A <mark>&lt;promise&gt;</mark>.');
  assert.equal(run('evidenceText({text:"A <promise>."},{quote:"invented",quote_matched:false},false)'),
    'A &lt;promise&gt;.');
  const colored = run('evidenceText({text:"alice attacks alaska"},{quote:"alice",quote_matched:true},false)');
  assert.match(colored,/<mark><span style="color:/);
  assert.match(colored,/data-place="alaska"/);
});

test('one trace span cited in several roles renders one evidence row with all excerpts highlighted', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3";
    EVAL.spans=[{span:145,turn:3,round:1,step:0,layer:"message",player:"alice",
      text:"I will secure Brazil, and we should stop Bob."}]; rebuildTrace();
    globalThis.unit={kind:"commitment",id:"c1",annotator:0,actor:"alice",
      passage:{span:145,quote:"I will secure Brazil",resolved:true,quote_matched:true},
      establishment:[{span:145,quote:"I will secure Brazil",resolved:true,quote_matched:true}],
      proposals:[{evidence:[{span:145,quote:"we should stop Bob",resolved:true,quote_matched:true}],responses:[]}],
      status_history:[{status:"made",cutoff_span:145,evidence:[{span:145,quote:"I will secure Brazil",resolved:true,quote_matched:true}]}],
      revisions:[],mentions:[],trigger:[],outcome:[]};`);
  const html = run('unitEvidenceV3(unit)');
  assert.equal((html.match(/id="tagEvidenceSpan-145"/g) || []).length,1);
  assert.equal((html.match(/class="evquote"/g) || []).length,1);
  assert.match(html,/<mark>I will secure Brazil<\/mark>/);
  assert.match(html,/<mark>we should stop Bob<\/mark>/);
  assert.equal((html.match(/I will secure Brazil/g) || []).length,1);
});

test('two-tag rows show one status per tag, full passages, and structured evidence', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3"; EVAL.version="new"; EVAL.has_evaluation=true;
    EVAL.members=[{index:0,label:"Coder",status:"complete",coverage:[],statements:[
      {id:"s1",actor:"alice",passage:{span:0,quote:"A <promise>.",resolved:true,quote_matched:true},
       proposition:"A claim",basis:"stated_plan",relevant_time:"now",status:"deceptive",comparison:[],
       deliberate_intent:[],context:[],explanation:"Explicit intent",valid:true,issues:[],mentions:[]}],
       commitments:[{id:"c1",actor:"alice",passage:{span:0,quote:"A <promise>.",resolved:true,quote_matched:true},
       established:false,agreement_id:null,initiator:"alice",role:"proposer",counterparties:[],third_party_targets:[],
       terms:{action:"Wait",target:null,condition:null,start:null,deadline:null,restraint:false},
       establishment:[],proposals:[],mentions:[],revisions:[],trigger:[],outcome:[],
       interval:{start_span:0,end_span:0,complete:false,endpoint:"cutoff"},status:"proposed",status_history:[
       {status:"proposed",cutoff_span:0,reason:null,evidence:[]}],reason:null,explanation:"Proposal only",valid:true,issues:[]}]}];
    state.filters.annotators.add(0);`);
  const html = run('researchHtml()');
  assert.match(html,/Statements/);
  assert.match(html,/Commitments/);
  assert.match(html,/A &lt;promise&gt;\./);
  assert.match(html,/deceptive/);
  assert.match(html,/class="tagNumber">1/);
  assert.match(html,/class="tagNumber">2/);
  const turnTags = run('orderedV3Units().map(unitPill).join("")');
  assert.match(turnTags,/class="pill turnTag tagKind-statement[^\"]*"/);
  assert.match(turnTags,/class="pill turnTag tagKind-commitment[^\"]*"/);
  assert.match(turnTags,/class="turnTagNumber">1<\/span>/);
  assert.match(turnTags,/class="turnTagNumber">2<\/span>/);
  assert.match(turnTags,/class="turnTagStatus researchTone-deceptive">deceptive<\/span>/);
  assert.match(turnTags,/class="turnTagStatus researchTone-open">proposed<\/span>/);
  assert.doesNotMatch(turnTags,/#1|#2/);
  assert.doesNotMatch(html.replace(/<[^>]*>/g,''),/\b(?:s1|c1)\b/);
  run('openUnitDrawer("0:commitment:c1")');
  const panel = run('el("panelRoot").innerHTML');
  assert.match(panel,/class="drawer unitDrawer/);
  assert.doesNotMatch(panel,/class="drawer rawDrawer/);
  assert.doesNotMatch(panel,/Exact original terms/);
  assert.match(panel,/class="tagTimeline"/);
  assert.match(panel,/class="tagEvent tagKind-commitment researchTone-open selected"/);
  assert.match(panel,/class="tagEvent tagKind-statement researchTone-deceptive"/);
  assert.match(panel,/class="tagName"/);
  assert.match(panel,/A &lt;promise&gt;\./);
  assert.match(panel,/No undertaking established/);
  assert.match(panel,/class="tagEvidence" id="tagEvidence"/);
  assert.doesNotMatch(panel,/class="tagEvidence" id="tagEvidence" open/);
  assert.match(panel,/jumpToTagEvidence\(0\)/);
  assert.match(panel,/openLineDrawer\(0\)/);
  assert.doesNotMatch(panel,/Full message|Citation review:|unitEvidenceRole/);
  assert.doesNotMatch(panel,/A claim|stated plan|Reference c1|Agreement A_/);
  const fullName = run('unitPlayer("alice:model-v3")');
  assert.match(fullName,/alice:model-v3/);
  assert.doesNotMatch(fullName,/class="unitPlayer"/);
  assert.doesNotMatch(panel,/condition<\/dt><dd>Unknown/);
  run('EVAL.members[0].commitments[0].needs_review=true; EVAL.members[0].commitments[0].issues=["Invalid citation [69]"]; renderPanels()');
  assert.match(run('el("panelRoot").innerHTML'),/Needs review/);
  assert.doesNotMatch(run('el("panelRoot").innerHTML'),/Invalid citation \[69\]|Citation review:/);
  assert.equal(run('jumpToTagEvidence(0); el("tagEvidence").open'),true);
  run('renderPanels()');
  assert.match(run('el("panelRoot").innerHTML'),/id="tagEvidence" open/);
  run('openUnitDrawer("0:commitment:c1")');
  const collapsed = run('el("panelRoot").innerHTML');
  assert.match(collapsed,/class="drawer unitDrawer/);
  assert.doesNotMatch(collapsed,/class="tagEvent tagKind-commitment researchTone-open selected"/);
  assert.doesNotMatch(collapsed,/No undertaking established/);
  run('openUnitDrawer("0:commitment:c1")');
  assert.doesNotMatch(run('el("panelRoot").innerHTML'),/id="tagEvidence" open/);
  run('openLineDrawer(0)');
  const both = run('el("panelRoot").innerHTML');
  assert.match(both,/class="drawer unitDrawer/);
  assert.match(both,/class="drawer rawDrawer withUnit/);
  run('closeDrawer()');
  assert.match(run('el("panelRoot").innerHTML'),/class="drawer unitDrawer/);
  const field = run(`evidenceQuote({kind:"statement",id:"s1",annotator:0,turn:1},
    {span:0,resolved:true,field_path:["board","brazil"],resolved_field_value:["alice",2],field_matched:true})`);
  assert.match(field,/Brazil: 2 armies · alice/);
  run(`EVAL.spans.push({span:1,turn:1,round:1,step:1,layer:"action",player:"alice",
    text:"alice places 2 armies on brazil"}); rebuildTrace();`);
  const action = run(`evidenceQuote({kind:"commitment",id:"c1",annotator:0,turn:1},
    {span:1,resolved:true,field_path:["action"],resolved_field_value:{kind:"place_armies",count:2},field_matched:true})`);
  assert.match(action,/places 2 armies on brazil/);
  assert.doesNotMatch(action,/action =|applied action/);
});

test('two-tag timeline orders passages and keeps commitment changes beside the selected tag', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3"; EVAL.version="new"; EVAL.has_evaluation=true;
    EVAL.spans=[
      {span:0,turn:1,round:1,step:0,layer:"message",player:"alice",text:"I will wait."},
      {span:1,turn:1,round:1,step:0,layer:"message",player:"alice",text:"Accepted."},
      {span:2,turn:2,round:1,step:0,layer:"message",player:"alice",text:"We agreed."},
      {span:3,turn:4,round:2,step:0,layer:"action",player:"alice",text:"Attacked."}
    ]; rebuildTrace();
    EVAL.members=[{index:0,label:"Coder",coverage:[],
      statements:[{id:"S_r1_pact_exists",actor:"alice",passage:{span:2,quote:"We agreed."},
        proposition:"An agreement exists",basis:"recorded_fact",relevant_time:"Round 1",
        status:"supported",explanation:"The acceptance supports this.",comparison:[],deliberate_intent:[],
        context:[],mentions:[],valid:true,issues:[]}],
      commitments:[{id:"C_r1_noattack",actor:"alice",passage:{span:0,quote:"I will wait."},
        status:"violated",established:true,explanation:"The later attack broke the promise.",
        terms:{action:"Avoid attacks",deadline:"End of round 1"},agreement_id:"A_r1_noattack",
        proposals:[{author:"alice",terms:"Wait through round 1",evidence:[{span:0,quote:"I will wait."}],
          responses:[{player:"alice",status:"accepted",evidence:[{span:1,quote:"Accepted."}]}]}],
        revisions:[],mentions:[],establishment:[],trigger:[],outcome:[{span:3,quote:"Attacked.",resolved:true}],
        status_history:[{status:"made",cutoff_span:1,reason:null,evidence:[]},
          {status:"violated",cutoff_span:3,reason:"Attacked",evidence:[]}],
        valid:true,issues:[]}]}]; state.filters.annotators.add(0);`);
  const list = run('researchHtml()');
  assert.ok(list.indexOf('I will wait.') < list.indexOf('We agreed.'));
  assert.match(list,/Round 1 · Turn 1/);
  assert.match(list,/Round 1 · Turn 2/);
  run('openUnitDrawer("0:commitment:C_r1_noattack")');
  let panel = run('el("panelRoot").innerHTML');
  assert.match(panel,/class="tagNumber">1/);
  assert.match(panel,/class="tagNumber">2/);
  assert.match(panel,/<strong>made<\/strong> ·/);
  assert.match(panel,/<strong>violated<\/strong> ·/);
  assert.doesNotMatch(panel,/Proposed by|accepted/);
  assert.match(panel,/Round 2 · Turn 4/);
  assert.match(panel,/jumpToTagEvidence\(3\)/);
  assert.match(panel,/openLineDrawer\(3\)/);
  assert.doesNotMatch(panel,/Establishment ·|response · Status: made|Full message|Citation review:/);
  assert.doesNotMatch(panel.replace(/<[^>]*>/g,''),/C_r1_noattack|A_r1_noattack|Action|Target|Related commitment/);
  run('openUnitDrawer("0:statement:S_r1_pact_exists")');
  panel = run('el("panelRoot").innerHTML');
  assert.match(panel,/class="tagEvent tagKind-statement researchTone-coop selected"/);
  assert.match(panel,/The acceptance supports this/);
  assert.doesNotMatch(panel.replace(/<[^>]*>/g,''),/An agreement exists|recorded fact|Relevant time/);
});

test('one shared pact has one timeline tag and participant status histories', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3"; EVAL.has_evaluation=true;
    EVAL.spans=[{span:0,turn:1,round:1,step:0,layer:"message",player:"alice",text:"We agree."},
      {span:1,turn:1,round:1,step:0,layer:"message",player:"bob",text:"Agreed."},
      {span:2,turn:1,round:1,step:0,layer:"message",player:"carla",text:"Agreed."}]; rebuildTrace();
    const make = (id,actor,span) => ({id,actor,kind:"commitment",passage:{span,quote:span ? "Agreed." : "We agree."},
      agreement_id:"pact",initiator:"alice",role:span ? "acceptor" : "proposer",status:"fulfilled",established:true,
      explanation:"Observed through deadline",establishment:[],proposals:[],mentions:[],revisions:[],trigger:[],outcome:[],
      // Older ledgers copied the author's proposed event into invitee histories.
      status_history:[{status:"proposed",cutoff_span:0,evidence:[{span:0,quote:"We agree."}]},
        {status:"made",cutoff_span:span,evidence:[{span,quote:span ? "Agreed." : "We agree."}]},
        {status:"fulfilled",cutoff_span:2,evidence:[{span:2,quote:"Agreed."}]}],valid:true,verified:false});
    EVAL.members=[{index:0,label:"Coder",coverage:[],statements:[],commitments:[
      make("a","alice",0),make("b","bob",1),make("c","carla",2)]}];
    state.filters.annotators.add(0);`);
  assert.equal(run('orderedV3Units().length'),1);
  assert.equal(run('orderedV3Units()[0].members.length'),3);
  run('openUnitDrawer("0:commitment:b")');
  const panel = run('el("panelRoot").innerHTML');
  assert.equal((panel.match(/class="tagEvent tagKind-commitment/g) || []).length,1);
  assert.equal((panel.match(/<strong>proposed<\/strong>/g) || []).length,1);
  assert.equal((panel.match(/<strong>made<\/strong>/g) || []).length,2);
  assert.equal((panel.match(/<strong>fulfilled<\/strong>/g) || []).length,3);
  assert.match(panel,/alice/); assert.match(panel,/bob/); assert.match(panel,/carla/);
  assert.doesNotMatch(panel,/accepted|Proposed by|Related commitment/);
});

test('one arrangement groups distinct undertakings by the same actor with independent histories', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3"; EVAL.has_evaluation=true;
    EVAL.spans=[{span:0,turn:1,round:1,step:0,layer:"message",player:"alice",text:"Two promises."}]; rebuildTrace();
    EVAL.members=[{index:0,label:"Coder",coverage:[],statements:[],commitments:[
      {id:"first",kind:"commitment",actor:"alice",agreement_id:"pact",passage:{span:0,quote:"Two"},
       terms:"Attack Green",valid:true,established:true,status:"fulfilled",status_history:[{status:"fulfilled",cutoff_span:0,evidence:[{span:0,quote:"Two"}]}]},
      {id:"second",kind:"commitment",actor:"alice",agreement_id:"pact",passage:{span:0,quote:"promises"},
       terms:"Avoid Red",valid:true,established:true,status:"violated",status_history:[{status:"violated",cutoff_span:0,evidence:[{span:0,quote:"promises"}]}]}
    ]}]; state.filters.annotators.add(0);`);
  assert.equal(run('orderedV3Units().length'),1);
  assert.equal(run('orderedV3Units()[0].status'),'mixed');
  run('openUnitDrawer("0:commitment:first")');
  const panel = run('el("panelRoot").innerHTML');
  assert.equal((panel.match(/class="tagUndertaking"/g) || []).length,2);
  assert.match(panel,/Attack Green.*<strong>fulfilled<\/strong>.*Avoid Red.*<strong>violated<\/strong>/s);
  assert.match(panel,/class="tagUndertaking" open/);
  assert.match(panel,/1 fulfilled/); assert.match(panel,/1 violated/);
  assert.equal((panel.match(/id="tagEvidenceSpan-0"/g) || []).length,1);
  assert.equal(run('v3Number(allUnits()[0])'),run('v3Number(allUnits()[1])'));
  assert.equal((run('unitPlayersV3(orderedV3Units()[0])').match(/alice/g) || []).length,1);
  assert.match(run('unitPill(orderedV3Units()[0])'),/researchTone-coop.*1 fulfilled.*researchTone-adversarial.*1 violated/);
});

test('arrangement filters require an undertaking matching actor, status and validity together', () => {
  const run = reader();
  run(`state.filters.annotators.add(0);
    globalThis.group = {members:[
      {annotator:0,kind:"commitment",actor:"alice",status:"fulfilled",valid:true},
      {annotator:0,kind:"commitment",actor:"bob",status:"violated",valid:false}]};
    state.filters.actor="alice"; state.filters.status="violated";`);
  assert.equal(run('unitVisible(group)'),false);
  run('state.filters.actor="bob"');
  assert.equal(run('unitVisible(group)'),true);
  run('state.filters.validOnly=true');
  assert.equal(run('unitVisible(group)'),false);
});

test('shared pact selection stays open when another player accepts later', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3"; EVAL.has_evaluation=true;
    EVAL.members=[{index:0,label:"Coder",coverage:[],statements:[],commitments:[
      {id:"a",actor:"alice",passage:{span:0,quote:"A <promise>."},agreement_id:"pact",initiator:"alice",
       status:"proposed",established:false,explanation:"Proposed",status_history:[],valid:true,
       proposals:[],revisions:[],mentions:[],establishment:[],trigger:[],outcome:[]}]}];
    state.filters.annotators.add(0); openUnitDrawer("0:commitment:a");`);
  assert.equal(run('state.unitDrawer.key'),'0:commitment:pact');
  run(`EVAL.members[0].commitments.push({...EVAL.members[0].commitments[0],id:"b",actor:"bob",status:"made",
    established:true,explanation:"Accepted"}); renderPanels();`);
  assert.equal(run('state.unitDrawer.key'),'0:commitment:pact');
  assert.equal((run('el("panelRoot").innerHTML').match(/class="tagEvent tagKind-commitment/g) || []).length,1);
});

test('trace chip opens evidence and briefly highlights the cited row', () => {
  const run = reader();
  run(`requestAnimationFrame = fn => fn(); setTimeout = fn => { globalThis.clearFlash = fn; };
    const row = el("tagEvidenceSpan-7"); row.scrollIntoView = () => { globalThis.didScroll = true; };
    const classes = new Set(); row.classList = {add: name => classes.add(name),remove: name => classes.delete(name),
      contains: name => classes.has(name)};`);
  run('jumpToTagEvidence(7)');
  assert.equal(run('el("tagEvidence").open'),true);
  assert.equal(run('didScroll'),true);
  assert.equal(run('el("tagEvidenceSpan-7").classList.contains("evidenceFlash")'),true);
  run('clearFlash()');
  assert.equal(run('el("tagEvidenceSpan-7").classList.contains("evidenceFlash")'),false);
});

test('passages within one message follow their order in the delivered text', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3";
    EVAL.spans=[{span:0,turn:1,round:1,step:0,layer:"message",player:"alice",
      text:"I did not attack. I will wait."}]; rebuildTrace();
    EVAL.members=[{index:0,statements:[{id:"s",actor:"alice",passage:{span:0,quote:"I did not attack."}}],
      commitments:[{id:"c",actor:"alice",passage:{span:0,quote:"I will wait."}}]}];`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(orderedV3Units().map(u => u.kind))')),
    ['statement','commitment']);
});

test('two-tag dashboard counts proposals separately and treats deceptive as one statement', () => {
  const run = reader();
  run(`EVAL.tag_schema="two_tag_v3"; EVAL.has_evaluation=true;
    EVAL.members=[{index:0,label:"Coder",status:"complete",coverage:[],
      commitments:[{valid:true,established:false,status:"proposed",reason:null,action_provenance:"unknown"}],
      statements:[{valid:true,actor:"alice",passage:{span:0},proposition:"Claim",relevant_time:"now",
        basis:"stated_plan",status:"deceptive"}]}]; renderEvalStats();`);
  const html = run('el("evalSheet").innerHTML');
  assert.match(html,/0 established · 1 proposals/);
  assert.match(html,/Deceptive includes contradiction and counts once/);
  assert.match(html,/1 statements/);
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

test('compact histories show agreed and committed with working terms and evidence links', () => {
  const run = reader();
  run(`EVAL.has_evaluation=true; EVAL.tag_schema="two_tag_v3";
    EVAL.members=[{index:0,label:"Coder",status:"complete",coverage:[],statements:[],commitments:[
      {id:"a",actor:"alice",agreement_id:"pact",terms:"Mutual restraint through round 2.",
       passage:{span:0,quote:"A <promise>.",resolved:true,quote_matched:true},valid:true,established:true,status:"agreed",
       explanation:"Bob agreed.",requires_acceptance:["bob"],status_history:[
         {status:"proposed",cutoff_span:0,evidence:[{span:0,quote:"A <promise>."}],explanation:"Offer."},
         {status:"agreed",cutoff_span:1,evidence:[{span:1,quote:"Agreed."}],explanation:"Accepted."}]},
      {id:"b",actor:"bob",agreement_id:"pact",terms:"Mutual restraint through round 2.",
       passage:{span:1,quote:"Agreed.",resolved:true,quote_matched:true},valid:true,established:true,status:"agreed",
       explanation:"Accepted.",status_history:[{status:"agreed",cutoff_span:1,
         evidence:[{span:0,quote:"A <promise>."},{span:1,quote:"Agreed."}],explanation:"Accepted."}]},
      {id:"c",actor:"alice",terms:"Attack Brazil this turn.",passage:{span:2,quote:"I will attack Brazil."},
       valid:true,established:true,status:"committed",explanation:"Explicit undertaking.",status_history:[
         {status:"committed",cutoff_span:2,evidence:[{span:2,quote:"I will attack Brazil."}],explanation:"Explicit undertaking."}],
       revisions:[{terms:"Attack Brazil next turn instead.",effective:false,explanation:"No release agreed.",
         evidence:[{span:2,quote:"I will attack Brazil."}]}]}]}];
    EVAL.spans.push({span:1,turn:2,round:1,layer:"message",player:"bob",text:"bob: Agreed."},
      {span:2,turn:3,round:1,layer:"message",player:"alice",text:"alice: I will attack Brazil."});
    rebuildTrace(); state.filters.annotators.add(0); state.filters.showCommentary=true;`);
  assert.equal(run('orderedV3Units().length'),2);
  assert.equal(run('unitTitle(orderedV3Units()[0])'),'Mutual restraint through round 2.');
  const pact = run('unitDetailV3(orderedV3Units()[0])');
  assert.equal((pact.match(/<strong>agreed<\/strong>/g) || []).length,1);
  assert.match(pact,/jumpToTagEvidence\(1\)/);
  const unilateral = run('unitDetailV3(orderedV3Units()[1])');
  assert.match(unilateral,/<strong>committed<\/strong>/);
  assert.match(unilateral,/Not effective.*Attack Brazil next turn instead/);
  assert.doesNotMatch(pact + unilateral,/<strong>made<\/strong>|undefined/);
  assert.equal(run('summarizeMemberV3(EVAL.members[0]).established_count'),2);
});
