let tournamentContinueSubmitting = false;

function renderTournamentContinuation(data) {
  const remaining = Math.max(0, 1000 - data.total_games);
  el('tournamentAdditionalGames').max = remaining;
  el('tournamentContinueRun').disabled = tournamentContinueSubmitting || !data.can_continue;
  el('tournamentAdditionalGames').disabled = tournamentContinueSubmitting || !data.can_continue;
  el('tournamentContinueRun').textContent = tournamentContinueSubmitting ? 'Adding games…' : 'Add games';
  el('tournamentContinueHint').textContent = !remaining ? 'This tournament has reached the 1000-game limit.'
    : !data.can_continue ? 'The original launch configuration is unavailable.'
    : `Reuse the original players, evaluation setup and game options, with the next seeds. ` +
      `${data.seating_mode === 'cycle' ? 'Continues the seating cycle' : 'Uses random seating'}; ` +
      `up to ${data.parallel_games} games at once. Results join the same aggregate stats. Uses API tokens for model players and evaluators.`;
}

async function continueTournament(event) {
  event.preventDefault();
  if (tournamentContinueSubmitting || el('tournamentContinueRun').disabled) return;
  el('tournamentContinueError').textContent = '';
  el('tournamentContinueStatus').textContent = '';
  const games = Number(el('tournamentAdditionalGames').value);
  if (!Number.isInteger(games) || games < 1 || games + latestData.total_games > 1000) {
    el('tournamentContinueError').textContent = 'Enter a whole number of additional games, up to the 1000-game total limit.';
    return;
  }
  tournamentContinueSubmitting = true;
  renderTournamentContinuation(latestData);
  try {
    const response = await fetch(`${POLL_URL}/continue`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({games, expected_total_games: latestData.total_games}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    // Old evaluation collections stay pinned. Show latest per game to include the additions.
    selectedTournamentVersion = '';
    const url = new URL(location.href);
    url.searchParams.delete('version');
    history.replaceState(null, '', url);
    el('tournamentContinueStatus').textContent = `Added ${result.added_games} games · ${result.total_games} games in this tournament.`;
    // Update the count even if the report refresh fails, so another submission cannot reuse stale membership.
    latestData = {...latestData, total_games: result.total_games, can_continue: result.total_games < 1000};
    await fetchTournamentVersion();
  } catch (error) {
    el('tournamentContinueError').textContent = error.message;
  } finally {
    tournamentContinueSubmitting = false;
    renderTournamentContinuation(latestData);
  }
}
