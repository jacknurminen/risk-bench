let tournamentReplaySubmitting = false;

function renderTournamentReplay(data) {
  const slots = data.replay_slots || [];
  const select = el('tournamentReplayGame');
  const selectedId = select.value;
  select.innerHTML = slots.map(slot => `<option value="${evaluationEscape(slot.game_id)}">` +
    `Game ${slot.slot} · ${evaluationEscape(slot.game_id)}</option>`).join('');
  if (slots.some(slot => slot.game_id === selectedId)) select.value = selectedId;
  else if (slots.length) select.value = (slots.find(slot => !slot.reason) || slots[0]).game_id;
  const selected = slots.find(slot => slot.game_id === select.value);
  select.disabled = tournamentReplaySubmitting || !slots.length;
  el('tournamentReplayRun').disabled = tournamentReplaySubmitting || !selected || !!selected.reason;
  el('tournamentReplayRun').textContent = tournamentReplaySubmitting ? 'Starting replay…' : 'Replay from start';
  el('tournamentReplayHint').textContent = selected?.reason ||
    'Restarts this game with the same seed, seats and saved setup. The new attempt replaces the old one in current stats. ' +
    'Tournament size and seating cycle stay the same. Previous attempts are kept. Uses API tokens for model players and evaluators.';
  const attempts = data.previous_attempts || [];
  el('tournamentReplayHistory').hidden = !attempts.length;
  el('tournamentReplayHistory').innerHTML = '<summary>Previous game attempts (excluded from current stats)</summary><ul>' +
    attempts.map(attempt => `<li>Game ${attempt.slot}: ` + (attempt.available
      ? `<a href="/games/${encodeURIComponent(attempt.previous_game_id)}">${evaluationEscape(attempt.previous_game_id)}</a>`
      : evaluationEscape(attempt.previous_game_id) + ' · no recorded gameplay') + '</li>').join('') + '</ul>';
}

async function replayTournamentGame(event) {
  event.preventDefault();
  if (tournamentReplaySubmitting || el('tournamentReplayRun').disabled) return;
  const gameId = el('tournamentReplayGame').value;
  el('tournamentReplayError').textContent = '';
  el('tournamentReplayStatus').textContent = '';
  tournamentReplaySubmitting = true;
  renderTournamentReplay(latestData);
  try {
    const response = await fetch(`${POLL_URL}/games/${encodeURIComponent(gameId)}/replay`, {method: 'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    selectedTournamentVersion = '';
    const url = new URL(location.href);
    url.searchParams.delete('version');
    history.replaceState(null, '', url);
    // Keep the old attempt out of subsequent submissions even if refreshing the report fails.
    latestData = {...latestData, replay_slots: latestData.replay_slots.map(slot => slot.game_id === gameId
      ? {...slot, game_id: result.id, reason: 'The replay is running or queued.'} : slot)};
    el('tournamentReplayStatus').textContent = `Game ${result.slot} replay ${result.queued ? 'queued' : 'started'} · ` +
      `${result.total_games} games in this tournament.`;
    await fetchTournamentVersion();
    el('tournamentReplayGame').value = result.id;
  } catch (error) {
    el('tournamentReplayError').textContent = error.message;
  } finally {
    tournamentReplaySubmitting = false;
    renderTournamentReplay(latestData);
  }
}
