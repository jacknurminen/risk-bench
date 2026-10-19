const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const el = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '5', textContent: '', disabled: false});
    return nodes.get(id);
  };
  const context = vm.createContext({el, URL, latestData: {
    total_games: 5, parallel_games: 2, seating_mode: 'cycle', can_continue: true,
  }, POLL_URL: '/api/tournaments/t', selectedTournamentVersion: 'old',
  location: {href: 'http://localhost/tournaments/t?version=old'}, history: {replaceState() {}},
  event: {preventDefault() {}}, fetch: async () => {throw new Error('Unexpected request');},
  fetchTournamentVersion: async () => {},
  });
  vm.runInContext(readFileSync('ui/tournament-continue.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}

test('adding five games uses existing tournament and shows all latest evaluations', async () => {
  const run = setup();
  run(`let sent, sentUrl, changedUrl, refreshed = false;
    history.replaceState = (a,b,url) => changedUrl = String(url);
    fetchTournamentVersion = async () => { refreshed = true; };
    fetch = async (url, options) => {sentUrl = url; sent = JSON.parse(options.body);
      return {ok: true, json: async () => ({added_games: 5, total_games: 10})};};
    renderTournamentContinuation(latestData);`);
  assert.match(run("el('tournamentContinueHint').textContent"), /Continues the seating cycle/);
  await run('continueTournament(event)');
  assert.equal(run('sentUrl'), '/api/tournaments/t/continue');
  assert.equal(run('JSON.stringify(sent)'), JSON.stringify({games: 5, expected_total_games: 5}));
  assert.equal(run('selectedTournamentVersion'), '');
  assert.equal(run('changedUrl'), 'http://localhost/tournaments/t');
  assert.equal(run('refreshed'), true);
  assert.match(run("el('tournamentContinueStatus').textContent"), /Added 5 games · 10 games/);
  assert.equal(run('latestData.total_games'), 10);
});

test('duplicate clicks do not submit twice while awaiting the server', async () => {
  const run = setup();
  run(`let count = 0, resolve;
    fetch = () => {count++; return new Promise(r => resolve = r);};
    let first = continueTournament(event);`);
  await run('continueTournament(event)');
  assert.equal(run('count'), 1);
  assert.equal(run("el('tournamentContinueRun').disabled"), true);
  run(`resolve({ok:true, json:async()=>({added_games:5,total_games:10})})`);
  await run('first');
  assert.equal(run("el('tournamentContinueRun').disabled"), false);
});

test('stale submissions display the reason and preserve the selected evaluation version', async () => {
  const run = setup();
  run(`fetch = async () => ({ok:false, json:async()=>({detail:'The tournament changed. Refresh before adding more games.'})});`);
  await run('continueTournament(event)');
  assert.match(run("el('tournamentContinueError').textContent"), /tournament changed/);
  assert.equal(run('selectedTournamentVersion'), 'old');
  assert.equal(run('latestData.total_games'), 5);
  assert.equal(run('tournamentContinueSubmitting'), false);
});

test('invalid and excessive counts are blocked and unavailable configurations disable continuation', async () => {
  const run = setup();
  for (const value of ['', '0', '-2', '1.5', '1000']) {
    run(`el('tournamentAdditionalGames').value=${JSON.stringify(value)}`);
    await run('continueTournament(event)');
    assert.match(run("el('tournamentContinueError').textContent"), /whole number/);
  }
  run(`renderTournamentContinuation({...latestData,can_continue:false});`);
  assert.equal(run("el('tournamentContinueRun').disabled"), true);
  assert.match(run("el('tournamentContinueHint').textContent"), /configuration is unavailable/);
  run(`renderTournamentContinuation({...latestData,total_games:1000,can_continue:false});`);
  assert.match(run("el('tournamentContinueHint').textContent"), /1000-game limit/);
});

test('continuation controls stay outside the evaluation and game context tabs', () => {
  const html = readFileSync('ui/tournament.html.j2', 'utf8');
  assert.ok(html.indexOf('id="tournamentContinueForm"') < html.indexOf('<nav class="tournamentTabs"'));
  assert.match(html, /renderTournamentContinuation\(data\)/);
  assert.match(html, /addEventListener\('submit', continueTournament\)/);
});
