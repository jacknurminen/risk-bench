const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const el = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', textContent: '', disabled: false});
    return nodes.get(id);
  };
  const context = vm.createContext({el, URL, encodeURIComponent,
    latestData: {total_games: 5, replay_slots: [
      {slot: 1, game_id: 't-g1', reason: 'Pause the game first.'},
      {slot: 2, game_id: 't-g2', reason: null},
    ], previous_attempts: []}, POLL_URL: '/api/tournaments/t', selectedTournamentVersion: 'old',
    location: {href: 'http://localhost/tournaments/t?version=old'}, history: {replaceState() {}},
    event: {preventDefault() {}}, fetch: async () => {throw new Error('Unexpected request');},
    fetchTournamentVersion: async () => {},
  });
  vm.runInContext(readFileSync('ui/tournament-evaluate.js', 'utf8').replace('let selectedTournamentVersion = null;', ''), context);
  vm.runInContext(readFileSync('ui/tournament-replay.js', 'utf8'), context);
  vm.runInContext('renderTournamentReplay(latestData)', context);
  return code => vm.runInContext(code, context);
}

test('replay selects an available game and blocks active slots', () => {
  const run = setup();
  assert.equal(run("el('tournamentReplayGame').value"), 't-g2');
  assert.equal(run("el('tournamentReplayRun').disabled"), false);
  assert.match(run("el('tournamentReplayHint').textContent"), /same seed, seats/);
  run("el('tournamentReplayGame').value='t-g1'; renderTournamentReplay(latestData)");
  assert.equal(run("el('tournamentReplayRun').disabled"), true);
  assert.equal(run("el('tournamentReplayHint').textContent"), 'Pause the game first.');
});

test('replay replaces the selected attempt and switches from old evaluation versions to latest', async () => {
  const run = setup();
  run(`let calledUrl, calledOptions, changedUrl;
    history.replaceState = (a,b,url) => changedUrl = String(url);
    fetchTournamentVersion = async () => {};
    fetch = async (url,options) => { calledUrl=url; calledOptions=options; return {ok:true,
      json:async()=>({id:'t-g2-replay-new',slot:2,total_games:5,queued:true})}; };`);
  await run('replayTournamentGame(event)');
  assert.equal(run('calledUrl'), '/api/tournaments/t/games/t-g2/replay');
  assert.equal(run('calledOptions.method'), 'POST');
  assert.equal(run('selectedTournamentVersion'), '');
  assert.equal(run('changedUrl'), 'http://localhost/tournaments/t');
  assert.equal(run('latestData.total_games'), 5);
  assert.equal(run('latestData.replay_slots[1].game_id'), 't-g2-replay-new');
  assert.equal(run("el('tournamentReplayRun').disabled"), true);
  assert.match(run("el('tournamentReplayStatus').textContent"), /Game 2 replay queued · 5 games/);
});

test('busy and stale server errors preserve current stats and selected version', async () => {
  const run = setup();
  run(`fetch = async()=>({ok:false,json:async()=>({detail:'This game is no longer a current tournament attempt.'})});`);
  await run('replayTournamentGame(event)');
  assert.equal(run('selectedTournamentVersion'), 'old');
  assert.equal(run('latestData.replay_slots[1].game_id'), 't-g2');
  assert.match(run("el('tournamentReplayError').textContent"), /no longer/);
  assert.equal(run('tournamentReplaySubmitting'), false);
});

test('duplicate clicks submit once and history links original attempts', async () => {
  const run = setup();
  run(`let calls=0,resolve;
    fetchTournamentVersion = async () => {};
    fetch = () => {calls++; return new Promise(r=>resolve=r);};
    let first = replayTournamentGame(event);`);
  await run('replayTournamentGame(event)');
  assert.equal(run('calls'), 1);
  run(`resolve({ok:true,json:async()=>({id:'t-g2-replay-new',slot:2,total_games:5,queued:false})})`);
  await run('first');
  run(`latestData.previous_attempts=[{slot:2,previous_game_id:'t-g2',available:true},
    {slot:3,previous_game_id:'t-g3',available:false}]; renderTournamentReplay(latestData);`);
  const html = run("el('tournamentReplayHistory').innerHTML");
  assert.match(html, /excluded from current stats/);
  assert.match(html, /href="\/games\/t-g2"/);
  assert.doesNotMatch(html, /href="\/games\/t-g3"/);
});
