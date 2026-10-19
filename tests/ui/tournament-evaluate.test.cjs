const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const el = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'',textContent:'',innerHTML:'',children:[],disabled:false});
    return nodes.get(id);
  };
  const data = {id:'t',total_games:3,parallel_games:2,games:Array(3).fill({finished:true}),
    evaluation_versions:[],evaluation_version:null};
  const context = vm.createContext({el,latestData:data,POLL_URL:'/api/tournaments/t',URL,URLSearchParams,
    location:{href:'http://localhost/tournaments/t'},history:{replaceState(){}},
    fetch:async()=>{throw new Error('unexpected request');}});
  vm.runInContext(readFileSync('ui/tournament-evaluate.js','utf8'), context);
  return code => vm.runInContext(code, context);
}

test('bulk submit sends panel and method, then follows the returned tournament version', async () => {
  const run=setup();
  run(`tournamentEvalCatalog={}; el('tournamentEvalMethod').value='live_rounds';
    el('tournamentEvalRows').children=[{querySelector:selector=>({value:({
      '.batchProvider':'openai','.batchModel':'test-model','.batchEffort':'high'})[selector]})}];
    let requests=[]; fetch=async(url,options)=>{requests.push([url,options]);
      return {ok:true,json:async()=>options?.method==='POST'?{version:'20261011-010101-123456'}:
        {...latestData,evaluation_version:'20261011-010101-123456'}};};
    let rendered; function render(data){rendered=data;latestData=data;}`);
  await run('runTournamentEvaluation()');
  assert.deepEqual(JSON.parse(run('requests[0][1].body')), {
    evaluators:['openai:test-model:high'],live:true,restart:true,
  });
  assert.equal(run('requests[0][0]'),'/api/tournaments/t/evaluate');
  assert.match(run('requests[1][0]'),/version=20261011-010101-123456/);
  assert.equal(run('rendered.evaluation_version'),'20261011-010101-123456');
});

test('running batches disable duplicate runs even while an archived version is selected', () => {
  const run=setup();
  run(`tournamentEvalCatalog={}; latestData.evaluation_version='old';
    latestData.evaluation_batch={games:{g1:{status:'complete',versions:['old'],review_flags:2}}};
    latestData.evaluation_versions=[{id:'new',method:'whole_game',panel:['p'],created_at:'2026-10-11',
      games:{g1:{status:'running',versions:['new']},g2:{status:'queued',versions:['new']}}}];
    renderTournamentEvaluations(latestData);`);
  assert.equal(run("el('tournamentEvalRun').disabled"),true);
  assert.match(run("el('tournamentEvalStatus').textContent"),/1 complete with review flags/);
  assert.match(run("el('tournamentEvalJobs').innerHTML"),/version=old/);
  assert.equal(run('selectedTournamentVersion'),'old');
});

test('version fetch ignores stale responses and always pins polling to the chosen version', async () => {
  const run=setup();
  run(`let responses=[];let urls=[];let rendered=[];function render(data){rendered.push(data);}
    fetch=(url)=>{urls.push(url);return new Promise(resolve=>responses.push(resolve));};
    selectedTournamentVersion='first'; let first=fetchTournamentVersion();
    selectedTournamentVersion='second'; let second=fetchTournamentVersion();
    responses[1]({ok:true,json:async()=>({evaluation_version:'second'})});`);
  await run('second');
  run(`responses[0]({ok:true,json:async()=>({evaluation_version:'first'})});`);
  await run('first');
  assert.equal(run('rendered.length'),1);
  assert.equal(run('rendered[0].evaluation_version'),'second');
  assert.match(run('urls[1]'),/version=second/);
});

test('failed submissions leave prior versions intact and display the server reason', async () => {
  const run=setup();
  run(`tournamentEvalCatalog={}; el('tournamentEvalRows').children=[{querySelector:()=>({value:'test'})}];
    selectedTournamentVersion='old'; fetch=async()=>({ok:false,json:async()=>({detail:'Game is busy'})});`);
  await run('runTournamentEvaluation()');
  assert.equal(run('selectedTournamentVersion'),'old');
  assert.equal(run("el('tournamentEvalError').textContent"),'Game is busy');
  assert.equal(run('tournamentEvalSubmitting'),false);
});
