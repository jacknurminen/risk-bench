/* Tournament reruns are pinned collections of per-game evaluation versions. */
let tournamentEvalCatalog = null;
let selectedTournamentVersion = null;
let tournamentEvalSubmitting = false;
let tournamentRequestSequence = 0;
const evaluationEscape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function renderTournamentEvaluations(data) {
  if (selectedTournamentVersion === null) selectedTournamentVersion = data.evaluation_version || '';
  const versions = data.evaluation_versions || [];
  const select = el('tournamentEvalVersion');
  select.innerHTML = '<option value="">Latest per game</option>' + versions.map(version => {
    const label = version.label || `${new Date(version.created_at).toLocaleString()} · ` +
      `${version.method === 'live_rounds' ? 'Round replay' : 'Whole game'} · ${version.panel.join(', ')}`;
    return `<option value="${evaluationEscape(version.id)}">${evaluationEscape(label)}</option>`;
  }).join('');
  select.value = selectedTournamentVersion;
  const active = versions.find(version => Object.values(version.games).some(job => ['queued', 'running'].includes(job.status)));
  const selected = data.evaluation_batch || active;
  const counts = {};
  for (const job of Object.values(selected?.games || {})) {
    const status = job.status === 'complete' && job.review_flags ? 'complete with review flags' : job.status;
    counts[status] = (counts[status] || 0) + 1;
  }
  el('tournamentEvalStatus').textContent = Object.entries(counts).map(([status, count]) => `${count} ${status}`).join(' · ');
  const ready = data.games.length === data.total_games && data.games.every(game => game.finished);
  el('tournamentEvalRun').disabled = tournamentEvalSubmitting || !!active || !ready || !tournamentEvalCatalog;
  el('tournamentEvalRun').textContent = active ? 'Evaluations in progress' : 'Rerun all evaluations';
  el('tournamentEvalHint').textContent = ready
    ? `Runs all ${data.total_games} games, up to ${data.parallel_games} at once. Uses API tokens; previous versions are retained.`
    : 'Finish all games before rerunning tournament evaluations.';
  el('tournamentEvalJobs').innerHTML = selected ? '<summary>Per-game evaluation status</summary><ul>' +
    Object.entries(selected.games).map(([gameId, job]) => {
      const query = new URLSearchParams({version: job.versions[0] || ''});
      return `<li><a href="/games/${encodeURIComponent(gameId)}?${query}">${evaluationEscape(gameId)}</a>: ` +
        `${evaluationEscape(job.status)}${job.review_flags ? ` · ${job.review_flags} review flags` : ''}` +
        `${job.error ? ` — ${evaluationEscape(job.error)}` : ''}</li>`;
    }).join('') + '</ul>' : '';
  el('tournamentEvalJobs').hidden = !selected;
}

function addTournamentEvaluator(spec) {
  const providers = Object.keys(tournamentEvalCatalog.models_by_provider);
  const preferred = tournamentEvalCatalog.models_by_provider.openai?.some(model => model.id === 'gpt-6-luna')
    ? 'openai:gpt-6-luna:medium' : `${providers[0]}:${tournamentEvalCatalog.models_by_provider[providers[0]][0].id}:medium`;
  const [provider, model, effort] = (spec || preferred).split(':');
  const row = document.createElement('div');
  row.className = 'controls';
  const options = values => values.map(value => `<option value="${evaluationEscape(value)}">${evaluationEscape(value)}</option>`).join('');
  row.innerHTML = `<select class="batchProvider" aria-label="Evaluator provider">${options(providers)}</select>` +
    '<select class="batchModel" aria-label="Evaluator model"></select>' +
    `<select class="batchEffort" aria-label="Reasoning effort">${options(tournamentEvalCatalog.efforts)}</select>` +
    '<button type="button" class="btn">Remove</button>';
  row.querySelector('.batchProvider').value = providers.includes(provider) ? provider : providers[0];
  const sync = selected => {
    const models = tournamentEvalCatalog.models_by_provider[row.querySelector('.batchProvider').value];
    row.querySelector('.batchModel').innerHTML = options(models.map(model => model.id));
    if (models.some(model => model.id === selected)) row.querySelector('.batchModel').value = selected;
  };
  sync(model);
  row.querySelector('.batchProvider').onchange = () => sync();
  row.querySelector('.batchEffort').value = tournamentEvalCatalog.efforts.includes(effort) ? effort : 'medium';
  row.querySelector('button').onclick = () => row.remove();
  el('tournamentEvalRows').appendChild(row);
}

async function initTournamentEvaluations() {
  el('tournamentEvalVersion').onchange = async event => {
    selectedTournamentVersion = event.target.value;
    const url = new URL(location.href);
    if (selectedTournamentVersion) url.searchParams.set('version', selectedTournamentVersion);
    else url.searchParams.delete('version');
    history.replaceState(null, '', url);
    await fetchTournamentVersion();
  };
  el('tournamentEvalRun').onclick = runTournamentEvaluation;
  el('tournamentEvalAdd').onclick = () => addTournamentEvaluator();
  try {
    const response = await fetch('/api/models');
    if (!response.ok) throw new Error('Could not load evaluator models. Reload to retry.');
    tournamentEvalCatalog = await response.json();
    const previous = latestData.evaluation_versions?.find(version => version.panel.length);
    const panel = previous?.panel || [...new Set((latestData.research?.cohorts || []).map(cohort => cohort.evaluator))];
    (panel.length ? panel : [null]).forEach(addTournamentEvaluator);
    el('tournamentEvalMethod').value = previous?.method || 'whole_game';
    el('tournamentEvalAdd').disabled = false;
    renderTournamentEvaluations(latestData);
  } catch (error) {
    el('tournamentEvalError').textContent = error.message;
  }
}

async function runTournamentEvaluation() {
  if (tournamentEvalSubmitting || el('tournamentEvalRun').disabled) return;
  const evaluators = [...el('tournamentEvalRows').children].map(row =>
    `${row.querySelector('.batchProvider').value}:${row.querySelector('.batchModel').value}:${row.querySelector('.batchEffort').value}`);
  el('tournamentEvalError').textContent = '';
  if (!evaluators.length) { el('tournamentEvalError').textContent = 'Add at least one evaluator.'; return; }
  tournamentEvalSubmitting = true;
  renderTournamentEvaluations(latestData);
  try {
    const live = el('tournamentEvalMethod').value === 'live_rounds';
    const response = await fetch(`${POLL_URL}/evaluate`, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({evaluators, live, restart:live})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    selectedTournamentVersion = result.version;
    const url = new URL(location.href);
    url.searchParams.set('version', result.version);
    history.replaceState(null, '', url);
    await fetchTournamentVersion();
  } catch (error) {
    el('tournamentEvalError').textContent = error.message;
  } finally {
    tournamentEvalSubmitting = false;
    renderTournamentEvaluations(latestData);
  }
}

async function fetchTournamentVersion() {
  const sequence = ++tournamentRequestSequence;
  const version = selectedTournamentVersion || '';
  try {
    const response = await fetch(POLL_URL + (version ? '?' + new URLSearchParams({version}) : ''), {cache:'no-store'});
    if (!response.ok) throw new Error('Could not load this evaluation version. Showing the last loaded results.');
    const data = await response.json();
    if (sequence === tournamentRequestSequence && version === (selectedTournamentVersion || '')) render(data);
  } catch (error) {
    if (sequence === tournamentRequestSequence) {
      el('tournamentEvalError').textContent = error.message;
      // Keep the selector consistent with the report that is still on screen.
      el('tournamentEvalVersion').value = latestData.evaluation_version || '';
    }
  }
}
