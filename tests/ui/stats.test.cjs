const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function reader() {
  const nodes = new Map();
  const context = vm.createContext({document: {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {hidden: true});
    return nodes.get(id);
  }}, window: {BOOT: {
    game: {players: [{name: 'Alice <AI>'}, {name: 'Bob'}, {name: 'Silent'}], continents: [],
      steps: [0, 0, 1, 1, 2, 3].map(turn => ({board: {}, turn})),
      messages: [
        {sender: 'Alice <AI>', visibility: 'public', text: '  hello\nthere  '},
        {sender: 'Alice <AI>', visibility: 'private', recipient: 'Bob', text: 'one two three four'},
        {sender: 'Bob', visibility: 'public', text: '   '},
      ],
      thoughts: [{player: 'Alice <AI>', text: 'one two three'}, {player: 'Alice <AI>', text: 'four'}],
      notes: [{player: 'Alice <AI>', text: 'not a message or thought'}],
      stats: {series: {turns: [], players: []}, per_player: Array.from({length: 3}, () =>
        ({attacks: 0, conquests: 0, armies_lost: 0, public_messages: 0, private_messages: 0}))}},
    evaluation: {}, urls: {},
  }}});
  for (const file of ['state', 'stats']) vm.runInContext(readFileSync(`ui/reader/${file}.js`, 'utf8'), context);
  return code => vm.runInContext(code, context);
}

test('output averages count distinct game turns, both message visibilities and recorded thoughts', () => {
  const run = reader();
  const stats = JSON.parse(run('JSON.stringify(playerOutputStats(GAME))'));
  assert.equal(stats.turnCount, 4); // setup, plus three turns; repeated action snapshots are not turns
  assert.deepEqual(stats.rows[0], {name: 'Alice <AI>', publicMessages: 1, privateMessages: 1,
    messages: 2, messageWords: 6, thoughts: 2, thoughtWords: 4,
    messagesPerTurn: 0.5, thoughtsPerTurn: 0.5, wordsPerMessage: 3, wordsPerThought: 2});
  assert.equal(stats.rows[1].messages, 1); // receiving a private message is not output
  assert.equal(stats.rows[1].wordsPerMessage, 0);
  assert.equal(stats.rows[2].messagesPerTurn, 0); // silent turns remain in the denominator
  assert.equal(stats.rows[2].wordsPerMessage, null); // no entries is different from empty text
  assert.equal(stats.rows[2].wordsPerThought, null);
});

test('overview renders labeled output charts and refreshes from the latest game data', () => {
  const run = reader();
  run('renderStats()');
  let charts = run('el("outputCharts").innerHTML');
  assert.equal((charts.match(/<figure/g) || []).length, 4);
  assert.match(charts, /Alice &lt;AI&gt;/);
  assert.doesNotMatch(charts, /Alice <AI>|NaN|Infinity/);
  assert.match(charts, /0.5 msg \/ turn/);
  assert.match(charts, /No entries/);
  assert.match(run('el("outputNote").textContent'), /4 recorded turns, including setup as one turn/);
  assert.match(run('el("outputTable").innerHTML'), /scope="row"/);
  run('GAME.messages.push({sender:"Alice <AI>", visibility:"public", text:"more text"}); renderStats()');
  charts = run('el("outputCharts").innerHTML');
  assert.match(charts, /0.75 msg \/ turn/);
  assert.match(charts, /2.7 words \/ msg/);
});

test('missing optional logs and an empty timeline never produce invalid averages or bar widths', () => {
  const run = reader();
  run('GAME.steps = []; delete GAME.messages; delete GAME.thoughts; renderPlayerOutput()');
  const rows = JSON.parse(run('JSON.stringify(playerOutputStats(GAME).rows)'));
  assert.ok(rows.every(row => row.messages === 0 && row.thoughts === 0 && row.messagesPerTurn === null));
  assert.doesNotMatch(run('el("outputCharts").innerHTML'), /NaN|Infinity|undefined/);
  assert.match(run('el("outputTable").innerHTML'), /—/);
});
