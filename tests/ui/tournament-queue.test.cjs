const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const player = {querySelector: selector => selector === '.providerTile.active'
    ? {dataset:{provider:'random'}} : {value:''}};
  const document = {
    addEventListener: () => {},
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, {value:''}); return nodes.get(id); },
    querySelectorAll: () => [player, player, player],
    querySelector: () => ({value:'whole_game'}),
  };
  const context = vm.createContext({document, location:{}, setInterval:()=>{},
    fetch:()=>new Promise(()=>{})});
  const script = readFileSync('ui/index.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInContext(script, context);
  return code => vm.runInContext(code, context);
}

test('launch sends separate total and parallel counts with the selected evaluation method', async () => {
  const run = setup();
  run(`el('totalGames').value='10'; el('parallelGames').value='2';
    el('evaluatorRow').querySelector=()=>({dataset:{provider:'off'}});
    fetch=async (url,options)=>{globalThis.sent=JSON.parse(options.body);
      return {ok:true,json:async()=>({id:'test',tournament:false})};};`);
  await run('launch()');
  assert.equal(run('sent.games'), 10);
  assert.equal(run('sent.parallel_games'), 2);
  assert.equal(run('sent.evaluation_method'), 'whole_game');
  assert.equal(run('sent.seating_mode'), 'random');
  assert.equal(run('sent.seating_orders'), undefined);
});

test('cycle toggle sends the mode without an editable schedule', async () => {
  const run = setup();
  run(`el('totalGames').value='12'; el('seatingCycle').checked=true;
    el('evaluatorRow').querySelector=()=>({dataset:{provider:'off'}});
    fetch=async (url,options)=>{globalThis.sent=JSON.parse(options.body);
      return {ok:true,json:async()=>({id:'test',tournament:false})};};`);
  await run('launch()');
  assert.equal(run('sent.seating_mode'), 'cycle');
  assert.equal(run('sent.games'), 12);
  assert.equal(run('sent.seating_orders'), undefined);
  assert.doesNotMatch(readFileSync('ui/index.html','utf8'), /id="seatingOrders"|Fill all seat orders/);
});

test('queued rows show queue cancellation without links to missing journals', () => {
  const run = setup();
  run(`let queued={id:'t-g3',seed:3,players:['a','b','c'],queued:true,started:false,
    running:false,finished:false,modified_at:'2026-10-11T10:00:00Z'};`);
  const html = run('gameRow(queued,true)');
  assert.match(html,/queued/);
  assert.match(html,/cancel queue/);
  assert.doesNotMatch(html,/location.href|resume|confirmDelete/);
  const group = run(`tournamentRows({id:'t',players:['a','b','c'],game_ids:['g1','g2','g3'],
    parallel_games:2,created_at:'2026-10-11T10:00:00Z'},[
    {...queued,id:'g1',queued:false,running:true}, {...queued,id:'g2',queued:false,running:true}, queued])`);
  assert.match(group,/2 running/);
  assert.match(group,/1 queued/);
  assert.match(group,/3 games · 2 at once/);
  assert.doesNotMatch(group,/1 paused/);
});
