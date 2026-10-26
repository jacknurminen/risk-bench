const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
function reader(spans = []) {
  const context = vm.createContext({window: {BOOT: {
    game: {players:[{name:'human'}], continents:[], steps:[{board:{}, turn:1}], result:null},
    evaluation: {spans}, urls:{}, live:true, live_running:false,
  }}});
  vm.runInContext(readFileSync('ui/reader/state.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}
test('worker state and pending decisions determine status, independent of replay availability', () => {
  const run = reader();
  assert.equal(run('readerStatus()'), 'Paused');
  assert.equal(run('gameRunning = true; readerStatus()'), 'Agents playing');
  assert.equal(run('humanPending = true; readerStatus()'), 'Waiting for you');
  assert.equal(run('gameRunning = false; readerStatus()'), 'Paused');
  assert.equal(run('GAME.result = {winner:"human"}; readerStatus()'), 'Game over');
  assert.equal(run('GAME.result = {}; readerStatus()'), 'Draw');
});
test('turn highlights count resolved events, not matching actions or private dialogue', () => {
  const lines = [
    ['action', 'human places 3 armies on alaska'],
    ['event', 'human placed 3 armies on alaska.'],
    ['event', 'human placed 2 armies on alberta.'],
    ['action', 'human attacks alberta from alaska with 3 dice up to 2 times'],
    ['event', 'human attacked alberta from alaska: losses 1, 0.'],
    ['event', 'human attacked alberta from alaska: losses 0, 1.'],
    ['event', 'human conquered alberta from alaska.'],
    ['thought', 'I WON and conquered everything!'],
  ];
  const run = reader(lines.map(([layer,text],i) => ({layer,text,span:i,turn:1,step:0})));
  assert.deepEqual(JSON.parse(run('JSON.stringify(turnHighlights(1))')), [
    {kind:'placement',count:5,label:'5 armies placed'},
    {kind:'attack',count:2,label:'2 battles'},
    {kind:'conquest',count:1,label:'1 conquest'},
  ]);
  assert.equal(run('turnSummary(1)'), 'human conquered alberta from alaska.');
});

test('round labels follow recorded snapshots across eliminations and live refreshes', () => {
  const run = reader();
  run(`steps.splice(0, steps.length,
    {board: {}, turn: 0, round: 0},
    {board: {}, turn: 3, round: 1, current_player: 'carla'},
    {board: {}, turn: 4, round: 2, current_player: 'bob'},
    {board: {}, turn: 6, round: 3, current_player: 'bob'});
    rebuildTrace();`);
  assert.equal(run('gameTimeLabel(0, 0)'), 'Setup');
  assert.equal(run('gameTimeLabel(3, roundByTurn[3])'), 'Round 1 · Turn 3');
  assert.equal(run('gameTimeLabel(6, roundByTurn[6])'), 'Round 3 · Turn 6');
  assert.equal(run('gameTimeLabel(9, undefined)'), 'Turn 9');
  run(`steps.push({board: {}, turn: 8, round: 4, current_player: 'bob'}); rebuildTrace();`);
  assert.equal(run('gameTimeLabel(8, roundByTurn[8])'), 'Round 4 · Turn 8');
});

test('sidebar shows full identities and marks only the experimental player without changing game names', () => {
  const players = [
    {name: 'claude-opus-5-5#2', mission: 'Test mission', experiment_profile: {id: 'deceiver', label: 'Deceiver'}},
    {name: 'claude-opus-5-5', mission: 'Test mission'},
    {name: 'gemini-4-argon', mission: 'Test mission'},
  ];
  const elements = {};
  const game = {players, continents: [], steps: [{board: {}, turn: 1, players: players.map(() => ({eliminated: false}))}], result: null};
  const before = JSON.stringify(players);
  const context = vm.createContext({
    window: {BOOT: {game, evaluation: {spans: []}, urls: {}}},
    document: {getElementById: id => elements[id] ??= {}},
  });
  vm.runInContext(readFileSync('ui/reader/state.js', 'utf8'), context);
  vm.runInContext(readFileSync('ui/reader/timeline.js', 'utf8'), context);
  vm.runInContext('renderFilters = () => {}; renderMissionToggle = () => {}; renderPlayerHands = () => {}; renderRail();', context);
  const cards = elements.railPlayers.innerHTML.split('</article>');
  assert.match(cards[0], />claude-opus-5-5#2<\/span>/);
  assert.match(cards[0], /class="experimentBadge"[^>]*>Deceiver<\/span>/);
  assert.match(cards[1], />claude-opus-5-5#1<\/span>/);
  assert.doesNotMatch(cards[1], /experimentBadge/);
  assert.match(cards[2], />gemini-4-argon<\/span>/);
  assert.doesNotMatch(cards[2], /experimentBadge/);
  assert.equal(JSON.stringify(players), before);
  players[0].experiment_profile.label = 'Mission <Prober>';
  vm.runInContext('renderRail()', context);
  assert.match(elements.railPlayers.innerHTML, /Mission &lt;Prober&gt;/);
});
