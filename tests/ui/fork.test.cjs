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

test('the panel edits context only on a paused branch, and previews what the next prompt gains', async () => {
  const boxes = [{dataset: {player: 'alice'}, value: ''}, {dataset: {player: 'bob'}, value: ''}];
  const panel = {hidden: false, innerHTML: '', querySelectorAll: () => boxes, contains: () => false};
  const status = {textContent: ''};
  const preview = {innerHTML: ''};
  const posted = [];
  const sandbox = fork({
    elements: {forkPanel: panel, forkStatus: status, forkPreview: preview, driftNotice: {}},
    context: {
      fetch: async (url, options) => {
        posted.push({url, payload: options.body ? JSON.parse(options.body) : null});
        if (url.endsWith('/prompt-preview')) return {ok: true, json: async () => ({prompts: [
          {player: 'alice', request_kind: 'negotiation', is_next_request: true, system_prompt: 'You are alice',
           before: 'Board:\nasia', after: 'Board:\nAdditional information available to you:\nBob holds all of asia.\nasia'},
        ]})};
        return {status: 204, ok: true, json: async () => ({})};
      },
    },
  });

  vm.runInContext('renderForkPanel()', sandbox);
  assert.equal(panel.hidden, true, 'an ordinary game offers no context editor');

  sandbox.GAME.ancestry = {parent_game_id: 'parent', parent_cursor: '2:before'};
  sandbox.GAME.additional_context = {alice: 'kept from the fork'};
  vm.runInContext('renderForkPanel()', sandbox);
  assert.equal(panel.hidden, false);
  // what a player already knows is shown, but only as context for what you are adding
  assert.match(panel.innerHTML, /already knows<\/span>kept from the fork/);
  assert.match(panel.innerHTML, /\/games\/parent/);

  boxes[0].value = ' Bob holds all of asia. ';
  // half-written context survives the live poll, which calls this every second
  panel.innerHTML = '';
  vm.runInContext('renderForkPanel()', sandbox);
  assert.equal(panel.innerHTML, '', 'a panel with unsaved text is left alone');
  panel.innerHTML = 'rebuilt';

  sandbox.gameRunning = true;
  vm.runInContext('renderForkPanel()', sandbox);
  assert.equal(panel.hidden, true, 'a running branch takes no context edits');
  sandbox.gameRunning = false;
  vm.runInContext('renderForkPanel()', sandbox);
  assert.equal(panel.hidden, false);

  await vm.runInContext('previewForkPrompts', sandbox)();
  // the preview renders the whole section, so it is sent the inherited text joined with the addition
  assert.deepEqual(posted.at(-1), {url: '/api/games/parent/prompt-preview',
    payload: {context: {alice: 'kept from the fork\n\nBob holds all of asia.'}}});
  assert.match(preview.innerHTML, /<mark>Additional information available to you:\nBob holds all of asia.\n<\/mark>/);
  assert.match(preview.innerHTML, /changed/);

  await vm.runInContext('saveForkContext', sandbox)();
  // saving sends only the addition, with append set: an inherited briefing is never replaced
  assert.deepEqual(posted.at(-2), {url: '/api/games/parent/context',
    payload: {context: {alice: 'Bob holds all of asia.'}, append: true}});
  assert.equal(sandbox.GAME.additional_context.alice, 'kept from the fork\n\nBob holds all of asia.');
  assert.deepEqual(posted.at(-1), {url: '/api/games/parent/resume', payload: null});
  assert.equal(status.textContent, 'Running.');
  assert.deepEqual(boxes.map(box => box.value), ['', ''], 'committed additions leave the boxes empty');
});

test('overlapping save clicks send one edit and polling cannot duplicate its text', async () => {
  const boxes = [{dataset: {player: 'alice'}, value: 'new information'}];
  const panel = {hidden: false, innerHTML: 'editor', querySelectorAll: () => boxes, contains: () => false};
  const status = {textContent: ''};
  const posted = [];
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const sandbox = fork({
    elements: {forkPanel: panel, forkStatus: status},
    context: {
      fetch: async (url, options) => {
        posted.push({url, payload: options.body ? JSON.parse(options.body) : null});
        if (url.endsWith('/context')) await pending;
        return {status: 204, ok: true};
      },
    },
  });
  sandbox.GAME.ancestry = {parent_game_id: 'parent'};
  sandbox.GAME.additional_context = {alice: 'inherited'};
  const save = vm.runInContext('saveForkContext', sandbox);
  const button = {disabled: false};
  const first = save(button);
  assert.equal(button.disabled, true);
  assert.equal(boxes[0].disabled, true);
  await save(button);
  assert.equal(posted.length, 1);
  // A poll can see the committed edit before its POST response reaches the browser.
  sandbox.GAME.additional_context = {alice: 'inherited\n\nnew information'};
  vm.runInContext('renderForkPanel()', sandbox);
  assert.equal(panel.innerHTML, 'editor');
  release();
  await first;
  assert.equal(posted.length, 2);
  assert.equal(posted[1].url, '/api/games/parent/resume');
  assert.equal(sandbox.GAME.additional_context.alice, 'inherited\n\nnew information');
  assert.equal(button.disabled, false);
  assert.equal(boxes[0].disabled, false);
});

test('a failed context save keeps the draft and allows retrying', async () => {
  const boxes = [{dataset: {player: 'alice'}, value: 'new information'}];
  const panel = {querySelectorAll: () => boxes};
  const status = {textContent: ''};
  let attempts = 0;
  const sandbox = fork({
    elements: {forkPanel: panel, forkStatus: status},
    context: {fetch: async () => {
      attempts++;
      return {ok: false, json: async () => ({detail: 'Pause the game first.'})};
    }},
  });
  const save = vm.runInContext('saveForkContext', sandbox);
  const button = {disabled: false};
  await save(button);
  assert.equal(button.disabled, false);
  assert.equal(boxes[0].disabled, false);
  assert.equal(boxes[0].value, 'new information');
  assert.equal(status.textContent, 'Pause the game first.');
  await save(button);
  assert.equal(attempts, 2);
});
