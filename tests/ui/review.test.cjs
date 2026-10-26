const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function load() {
  const context = vm.createContext({});
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
  run('globalThis.answers = {qualifies: "correct", content: "correct", outcome: "unsure"}');
  assert.equal(run('collectVerdict({kind: "finding", unit_type: "commitment"}, f => answers[f])'), null);
  run('answers.citations = "not_applicable"');
  assert.deepEqual(
    JSON.parse(run('JSON.stringify(collectVerdict({kind: "finding", unit_type: "deception"}, f => answers[f]))')),
    {qualifies: 'correct', content: 'correct', outcome: 'unsure', citations: 'not_applicable'},
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

test('message items never render the evaluator labels', () => {
  const run = load();
  const html = run(`messageHtml({game_id: "g", judge: {commitments: ["SECRET"]},
    message: {span: 4, layer: "message", player: "alice", turn: 1, text: "I promise"},
    context: {earlier_messages: [], later_messages: [], private: []}})`);
  assert.match(html, /I promise/);
  assert.doesNotMatch(html, /SECRET/);
});
