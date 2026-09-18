const {test} = require('node:test');
const assert = require('node:assert/strict');
const Draft = require('../../ui/reader/human-draft.js');
const request = actions => ({request_id:'test', kind:'decision', view:{turn_number:1}, actions});
const placement = territory => ({kind:'place_armies', territory, count:7});
const attack = (from, to, dice=3) => ({kind:'attack', from_territory:from, to_territory:to, attacker_dice:dice});

test('click allocations are local, bounded, undoable, and committed together', () => {
  const prompt = request([placement('alaska'), placement('alberta')]);
  const before = JSON.stringify(prompt);
  const draft = new Draft(prompt);
  assert.equal(draft.add('enemy'), false);
  draft.select('alaska'); draft.select('alaska'); draft.select('alberta');
  assert.deepEqual(draft.preview, {alaska:2, alberta:1});
  assert.equal(draft.remaining, 4);
  assert.equal(draft.ready, false);
  assert.throws(() => draft.response());
  assert.equal(draft.allocate('alaska', 8), false);
  draft.undo(); assert.deepEqual(draft.allocations, {alaska:2});
  draft.add('alberta', draft.remaining);
  assert.equal(draft.ready, true);
  assert.equal(draft.add('alaska'), false);
  assert.deepEqual(draft.response(), {placements:[{action_index:0,count:2},{action_index:1,count:5}]});
  assert.equal(JSON.stringify(prompt), before);
  draft.clear(); assert.equal(draft.total, 0);
  draft.undo(); assert.equal(draft.total, 7);
  draft.allocate('alaska', 0); assert.deepEqual(draft.allocations, {alberta:5});
});

test('attacks select legal pairs and retain them only while legal in the same turn', () => {
  const prompt = request([attack('alaska','alberta'), attack('quebec','ontario'), {kind:'end_attack_phase'}]);
  const draft = new Draft(prompt);
  assert.equal(draft.select('alberta'), false);
  draft.select('alaska'); assert.equal(draft.ready, false);
  assert.equal(draft.select('ontario'), false);
  draft.select('alberta'); assert.equal(draft.ready, true);
  assert.deepEqual(draft.response(), {action_index:0});
  assert.deepEqual(draft.preview, {});
  const next = new Draft(prompt, draft);
  assert.equal(next.target, 'alberta');
  const captured = new Draft(request([attack('alaska','kamchatka')]), next);
  assert.equal(captured.source, 'alaska'); assert.equal(captured.target, '');
  const later = new Draft({...prompt, view:{turn_number:2}}, draft);
  assert.equal(later.source, '');
});

test('fortification previews both ends and enforces the move limit', () => {
  const draft = new Draft(request([{kind:'fortify',from_territory:'alaska',to_territory:'alberta',count:6},
    {kind:'skip_fortify'}]));
  draft.select('alaska'); draft.select('alberta'); draft.setCount(3);
  assert.deepEqual(draft.preview, {alaska:-3,alberta:3});
  assert.equal(draft.setCount(7), false); assert.equal(draft.setCount(0), false);
  assert.equal(draft.setCount(1.5), false);
  assert.deepEqual(draft.response(), {action_index:0,count:3});
  draft.setMode('skip_fortify'); assert.deepEqual(draft.response(), {action_index:1});
  assert.deepEqual(draft.preview, {});
});

test('forced trades and diplomacy work without map selections', () => {
  const trade = new Draft(request([{kind:'trade_cards',cards:[]}]));
  assert.equal(trade.mode, 'trade_cards'); assert.equal(trade.ready,true);
  assert.deepEqual(trade.response(),{action_index:0});
  const chat = new Draft({...request([]),kind:'negotiation'});
  assert.equal(chat.mode,'negotiation'); assert.deepEqual(chat.response(),{});
});

test('note-taking is ready without offering board actions', () => {
  const notes = new Draft({...request([]), kind:'notes', finished_turn:1});
  assert.equal(notes.mode, 'notes');
  assert.equal(notes.ready, true);
  assert.equal(notes.select('alaska'), false);
  assert.deepEqual(notes.preview, {});
  assert.deepEqual(notes.response(), {});
});
