const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

const CURSORS = [
  {before: '0:before', after: '0:after', stage: 'setup', next_description: 'alice gets a speaking opportunity'},
  {before: '1:before', after: '1:after', stage: 'negotiation_request', next_description: "Deliver alice's pending message, if any"},
  {before: '2:before', after: '2:after', stage: 'negotiation_delivery', next_description: 'bob gets a speaking opportunity'},
];

function fork({context = {}, elements = {}} = {}) {
  const toasts = [];
  const sandbox = {
    POLL_URL: '/api/games/parent',
    GAME: {players: [{name: 'alice'}, {name: 'bob'}], cursors: CURSORS, result: null, ancestry: {}, additional_context: {}},
    escapeHtml: value => (value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    colorOf: () => '#000',
    el: id => elements[id],
    toast: message => toasts.push(message),
    toasts,
    renderGameControl() {},
    document: {dispatchEvent() {}, activeElement: null},
    CustomEvent: class {},
    window: {location: {}},
    gameRunning: false,
    ...context,
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync('ui/reader/fork.js', 'utf8'), sandbox);
  return sandbox;
}

test('a seam describes the operation it cuts before and copies the game when pressed', async () => {
  let request = null;
  const sandbox = fork({context: {
    fetch: async (url, options) => {
      request = {url, payload: JSON.parse(options.body)};
      return {ok: true, json: async () => ({url: '/games/branch'})};
    },
  }});
  const separator = vm.runInContext("forkSeparator('2:before')", sandbox);
  assert.match(separator, /Fork from here/);
  assert.match(separator, /Next: Deliver alice&#39;s pending message|Next: Deliver alice's pending message/);
  assert.equal(vm.runInContext("forkSeparator('9:before')", sandbox), '', 'unknown cursors offer no seam');

  const button = {disabled: false, textContent: 'Fork from here'};
  await vm.runInContext('createFork', sandbox)('2:before', button);
  // The branch is created paused: the parent keeps running and the copy waits to be set up.
  assert.deepEqual(request, {url: '/api/games/parent/fork', payload: {cursor: '2:before', start: false}});
  assert.equal(sandbox.window.location.href, '/games/branch');
});

test('a fork refused by the server leaves the seam usable and says why', async () => {
  const sandbox = fork({context: {
    fetch: async () => ({ok: false, json: async () => ({detail: 'Cannot continue a terminal cursor.'})}),
  }});
  const button = {disabled: false, textContent: 'Fork from here'};
  await vm.runInContext('createFork', sandbox)('2:after', button);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Fork from here');
  assert.equal(sandbox.toasts.at(-1), 'Cannot continue a terminal cursor.');
  assert.equal(sandbox.window.location.href, undefined);
});
