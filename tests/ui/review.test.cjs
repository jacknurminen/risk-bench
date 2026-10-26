const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function load() {
  const context = vm.createContext({URLSearchParams});
  vm.runInContext(readFileSync('ui/review.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}

test('quotes are highlighted inside escaped span text', () => {
  const run = load();
  assert.equal(run('highlightQuote("a <b> c", "<b>")'), 'a <mark>&lt;b&gt;</mark> c');
  assert.equal(run('highlightQuote("plain", "absent")'), 'plain');
  assert.equal(run('highlightQuote("x & y", null)'), 'x &amp; y');
});

test('a verdict needs an answer to every question of its item type', () => {
  const run = load();
  assert.equal(run(`collectVerdict({kind: "finding", unit_type: "commitment"}, f => undefined)`), null);
  assert.deepEqual(
    JSON.parse(run(`JSON.stringify(collectVerdict({kind: "finding", unit_type: "deception"}, f => "unsure"))`)),
    {annotation: "unsure"},
  );
  assert.deepEqual(
    JSON.parse(run('JSON.stringify(collectVerdict({kind: "message"}, f => ({commitment: "yes", deception: "no"})[f]))')),
    {commitment: 'yes', deception: 'no'},
  );
});

test('next unreviewed item wraps around and reports when all are done', () => {
  const run = load();
  run('globalThis.items = [{verdict: 1}, {verdict: null}, {verdict: 1}, {verdict: null}]');
  assert.equal(run('nextOpenIndex(items, 1)'), 3);
  assert.equal(run('nextOpenIndex(items, 3)'), 1);
  assert.equal(run('nextOpenIndex(items.map(() => ({verdict: 1})), 0)'), -1);
});

test('queues run game by game in trace order and link into the pinned game view', () => {
  const run = load();
  run(`globalThis.sample = {id: "s1", runs: [{game_id: "g2", run_id: "r2"}, {game_id: "g1", run_id: "r1"}], items: [
    {item_id: "m1", kind: "message", game_id: "g1", span: 5}, {item_id: "m2", kind: "message", game_id: "g2", span: 9},
    {item_id: "m3", kind: "message", game_id: "g2", span: 3}, {item_id: "f1", kind: "finding", game_id: "g1", span: 1}]}`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(queueItems(sample, "message").map(i => i.item_id))')), ['m3', 'm2', 'm1']);
  assert.equal(run('reviewUrl("t1", sample, sample.items[0])'), '/games/g1?version=r1&review=t1%2Fs1&item=m1');
});

test('the shared review script does not redeclare the reader escape helper', () => {
  assert.doesNotMatch(readFileSync('ui/review.js', 'utf8'), /\bescapeHtml\b/);
});

test('verdicts saved per part fold into one finding answer', () => {
  const run = load();
  assert.equal(run('findingAnswer({annotation: "correct"})'), 'correct');
  assert.equal(run('findingAnswer({qualifies: "correct", content: "incorrect", outcome: "unsure", citations: "correct"})'), 'incorrect');
  assert.equal(run('findingAnswer({qualifies: "correct", content: "correct", outcome: "unsure", citations: "not_applicable"})'), 'unsure');
  assert.equal(run('findingAnswer({qualifies: "correct", content: "correct", outcome: "correct", citations: "not_applicable"})'), 'correct');
});
