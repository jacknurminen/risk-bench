const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup(fetchImplementation) {
  const context = vm.createContext({fetch: fetchImplementation, JSON});
  vm.runInContext(readFileSync('ui/about.js', 'utf8'), context);
  return code => vm.runInContext(code, context);
}

test('an untitled record falls back to a placeholder heading', () => {
  const run = setup(async () => { throw new Error('Unexpected request'); });
  assert.equal(run("aboutHeading({title: '', description: 'x'}, 'tournament')"), 'Untitled tournament');
  assert.equal(run("aboutHeading({title: 'Effort study', description: ''}, 'tournament')"), 'Effort study');
});

test('saving sends only the title and description and returns what the server stored', async () => {
  let sentUrl, sentOptions;
  const run = setup(async (url, options) => {
    sentUrl = url; sentOptions = options;
    return {ok: true, json: async () => ({title: 'Pilot', description: 'Note'})};
  });
  const saved = await run("saveAbout('/api/games/g/description', {title: ' Pilot ', description: 'Note', extra: 1})");
  assert.equal(sentUrl, '/api/games/g/description');
  assert.equal(sentOptions.method, 'PUT');
  assert.deepEqual(JSON.parse(sentOptions.body), {title: ' Pilot ', description: 'Note'});
  assert.equal(saved.title, 'Pilot');
});

test('a rejected save surfaces the validation message', async () => {
  const run = setup(async () => ({ok: false, status: 422,
    json: async () => ({detail: [{msg: 'String should have at most 200 characters'}]})}));
  await assert.rejects(run("saveAbout('/u', {title: 'x', description: ''})"), /at most 200 characters/);
});
