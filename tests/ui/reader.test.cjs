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
