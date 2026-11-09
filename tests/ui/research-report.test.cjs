const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
function report() {
  const context = vm.createContext({URLSearchParams});
  vm.runInContext(readFileSync('ui/research-report.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}
test('evidence links pin the original evaluation version and exact assessment', () => {
  const run = report();
  const html = run(`researchEvidenceLink({game_id:'game 1', version:'20260101-000000', index:2},
    'statement', 's1', '<evidence>')`);
  assert.match(html, /\/games\/game%201\?version=20260101-000000#unit=2%3Astatement%3As1/);
  assert.match(html, /&lt;evidence&gt;/);
  assert.match(run(`researchEvidenceLink({index:1}, 'commitment', 'c1', 'Open', true)`),
    /closeStats\(\); openUnitDrawer\(&quot;1:commitment:c1&quot;\)/);
});
test('missing evaluations are not presented as zero findings and historical scope is explicit', () => {
  const run = report();
  assert.match(run('renderTournamentResearch({cohorts:[],availability:[]}, [])'), /Missing evaluations are not zero findings/);
  run(`const p = {model:'Model <a>',kind:'agent',established:2,unestablished:1,outcomes:{fulfilled:1,violated:1},
    deception_incidents:2,deception_games:['g1'],games:['g1','g2'],observed_games:['g1','g2'],messages:20,
    turns_survived:8,coverage:{candidate:3,no_candidate:15,unclear:1,missing:1},excluded:1,
    roles:{initiator:1,accepting_participant:2,unknown:1},adherence:{fulfilled:1,resolved:2},specs:['test:model'],
    provenance:{agent_choice:2},records:[]};`);
  const html = run(`researchCohortHtml({evaluator:'Coder',replicate:1,codebook_version:'old-scope',players:[p],
    games:[{id:'g1',status:'complete',source_matches:true,partial:false}]})`);
  assert.match(html, /Historical statement schema/);
  assert.match(html, /not been reassessed/);
  assert.match(html, /Model &lt;a&gt;/);
  assert.match(html, /1 \/ 2/); // games, not incident count
  assert.match(html, /19 \/ 20/); // unclear is screened, missing is not
  assert.match(html, /1 \/ 2 \(50%\)/);
  assert.doesNotMatch(html, /honesty score|high-confidence signal|NaN|undefined/);
});
test('tournament research replaces the broad skill and conduct panel', () => {
  const html = readFileSync('ui/tournament.html.j2','utf8');
  assert.match(html, /Research evaluation/);
  assert.match(html, /renderTournamentResearch/);
  assert.match(html, /renderTournamentResearch\(data.research, data.games, data.output_volume\)/);
  assert.match(html, /if \(POLL_URL\) setTimeout\(refreshTournament/);
  assert.doesNotMatch(html, /conduct_deceptive|skill_strong|high-confidence signal|behavior tags/);
});

test('tournament output charts include unevaluated games, denominators, and live updates', () => {
  const run = report();
  run(`const output = {game_count:2,unfinished_games:1,includes_setup:true,players:[
    {name:'Alice <AI>',games:2,recorded_turns:8,messages:3,public_messages:2,private_messages:1,
      message_words:15,thoughts:2,thought_words:4,messages_per_game:1.5,messages_per_turn:0.375,
      words_per_message:5,thoughts_per_game:1,thoughts_per_turn:0.25,words_per_thought:2},
    {name:'Silent',games:2,recorded_turns:8,messages:0,public_messages:0,private_messages:0,
      message_words:0,thoughts:0,thought_words:0,messages_per_game:0,messages_per_turn:0,
      words_per_message:null,thoughts_per_game:0,thoughts_per_turn:0,words_per_thought:null}
  ]};`);
  let html = run('renderTournamentResearch({cohorts:[],availability:[]}, [], output)');
  assert.equal((html.match(/<figure/g) || []).length, 6);
  assert.match(html, /Player output volume/);
  assert.match(html, /includes games without evaluations/);
  assert.match(html, /Missing evaluations are not zero findings/);
  assert.match(html, /Alice &lt;AI&gt;/);
  assert.match(html, /1.5 msg \/ game/);
  assert.match(html, /0.38 msg \/ turn/);
  assert.match(html, /5 words \/ msg/);
  assert.match(html, /No entries/);
  assert.match(html, /including setup once per game/);
  assert.match(html, /hidden provider reasoning are excluded/);
  assert.match(html, /scope="row"/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined|Alice <AI>/);
  run('output.players[0].messages_per_game = 2');
  html = run('renderTournamentResearch({cohorts:[],availability:[]}, [], output)');
  assert.match(html, /2 msg \/ game/);
  assert.equal(run('tournamentOutputHtml(undefined)'), '');
  assert.equal(run('tournamentOutputHtml({players:[]})'), '');
});
test('tournament rows name each copy of a model and its experiment profile', () => {
  const run = report();
  const label = player => run(`researchTournamentPlayer(${JSON.stringify(player)}).model`);
  assert.equal(label({name: 'gpt', model: 'gpt'}), 'gpt');
  assert.equal(label({name: 'opus#2', model: 'opus'}), 'opus · opus#2');
  assert.equal(label({name: 'opus#3', model: 'opus', experiment_profile: 'Deceiver'}), 'opus · opus#3 (Deceiver)');
});
