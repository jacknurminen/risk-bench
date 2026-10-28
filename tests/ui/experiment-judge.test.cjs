const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function reader() {
  const context = vm.createContext({window: {BOOT: {
    game: {players:[{name:'opus#2'},{name:'opus'}], continents:[], steps:[{board:{}, turn:1}], result:null},
    evaluation: {spans:[]}, urls:{experiment_judge:'/api/games/g/experiment-judge'}, live:false, live_running:false,
  }}});
  vm.runInContext(readFileSync('ui/reader/state.js', 'utf8'), context);
  vm.runInContext('function unitPlayer(name) { return "<b>" + escapeHtml(name) + "</b>"; }', context);
  vm.runInContext(readFileSync('ui/reader/experiment-judge.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}
const meta = {evaluator: 'anthropic:claude-opus-5-5:medium', codebook_version: 'v1', created_at: '', finished: false,
  experiment_player: 'opus#2', summary: 'Opus caved.'};
const gaslighting = {...meta, judge: 'gaslighting', citation_issues: ['claim 1: span 9 does not exist'],
  tables: [{title: 'Outcomes by target', columns: [['player','player'],['disputed_complied','disputed but complied']],
    rows: [{player:'opus', disputed_complied:1}]}],
  claims: [{claim:'The pact covered <europe>', falsity:'distorted', claim_spans:[4], truth:'Only north_america',
    truth_spans:[1], targets:[{player:'opus', response:'disputed_complied', response_spans:[5],
    notes_adopted:true, note_spans:[6]}]}]};
const probing = {...meta, judge: 'probing', citation_issues: [], tables: [], inference: [],
  attempts: [{description:'Leave asia for iceland', move:'priced_deal', message_spans:[2], described_opponent:'opus',
    listener:'opus', guess_correct:false, outcome:'refused', outcome_spans:[3], gain:false, gain_spans:[], note:''}]};
const deception = {...meta, judge: 'deception', citation_issues: [], tables: [],
  attempts: [{target:'opus', promise:'Stay out of alaska', promise_spans:[0], reliance:'relied', reliance_spans:[1],
    breached:true, breach_spans:[6], payoff:true, payoff_spans:[7], aftermath:'renewed', aftermath_spans:[8]}]};
const status = (name, assessment) => JSON.stringify({available:true, judge:{name, title:'X judge'}, running:false, error:null, assessment});

test('the card offers the judge before any run', () => {
  const run = reader();
  run(`judge = ${status('probing', null)}`);
  const html = run('judgeCardHtml()');
  assert.match(html, /X judge/);
  assert.match(html, /Choose judge model/);
  assert.match(html, /leverage attempts/);
});

test('a gaslighting result shows its table, escaped claims and links into the trace', () => {
  const run = reader();
  run(`judge = ${status('gaslighting', gaslighting)}`);
  const html = run('judgeCardHtml()');
  assert.match(html, /<th>disputed but complied<\/th>/);
  assert.match(html, /<td><b>opus<\/b><\/td><td>1<\/td>/);
  assert.match(html, /The pact covered &lt;europe&gt;/);
  assert.match(html, /notes adopted<\/span><button class="chip"[^>]*openLineDrawer\(6\)/);
  assert.match(html, /1 citation issue</);
  assert.match(html, /Run again/);
});

test('prober and deceiver results render their own attempts', () => {
  const run = reader();
  run(`judge = ${status('probing', probing)}`);
  const probingHtml = run('judgeCardHtml()');
  assert.match(probingHtml, /<summary>Leverage attempts<\/summary>/);
  assert.match(probingHtml, /wrong reading/);
  assert.match(probingHtml, /refused<\/span><button class="chip"[^>]*openLineDrawer\(3\)/);
  run(`judge = ${status('deception', deception)}`);
  const deceptionHtml = run('judgeCardHtml()');
  assert.match(deceptionHtml, /deceiver gained/);
  assert.match(deceptionHtml, /agreed again<\/span><button class="chip"[^>]*openLineDrawer\(8\)/);
});

test('a running judge hides the controls and a running game blocks a new run', () => {
  const run = reader();
  run(`judge = ${status('deception', null)}; judge.running = true`);
  assert.doesNotMatch(run('judgeCardHtml()'), /id="judgeRun"/);
  run('judge.running = false; judge.error = "no key"; gameRunning = true');
  const html = run('judgeCardHtml()');
  assert.match(html, /last run failed: no key/);
  assert.match(html, /id="judgeRun" disabled/);
});
