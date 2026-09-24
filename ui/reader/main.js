/* ================= LIVE POLLING ================= */
let lastFingerprint = null;
async function refreshFromJournal() {
  const res = await fetch(POLL_URL, { cache: "no-store" }); if (!res.ok) return;
  const updated = await res.json();
  GAME.pause_error = updated.pause_error;
  gameRunning = updated.running;
  renderGameControl();
  renderForkPanel();
  document.dispatchEvent(new CustomEvent("reader:game-status"));
  const fingerprint = JSON.stringify([updated.steps.length, updated.steps.at(-1), updated.messages.length,
    updated.thoughts.length, updated.notes.length, updated.commentary.length, updated.cursors?.length, updated.result]);
  if (fingerprint === lastFingerprint) return; lastFingerprint = fingerprint;
  const following = index === steps.length - 1;
  steps.splice(0, steps.length, ...updated.steps);
  GAME.messages.splice(0, GAME.messages.length, ...updated.messages);
  GAME.thoughts.splice(0, GAME.thoughts.length, ...updated.thoughts);
  GAME.notes.splice(0, GAME.notes.length, ...updated.notes);
  GAME.commentary.splice(0, GAME.commentary.length, ...updated.commentary);
  GAME.commentators = updated.commentators ?? GAME.commentators;
  if (updated.result && !GAME.result && humanPlayers.size && !researchVisibilityChosen)
    state.filters.showCommentary = true;
  GAME.result = updated.result;
  GAME.cursors = updated.cursors;
  GAME.ancestry = updated.ancestry;
  GAME.additional_context = updated.additional_context;
  GAME.configuration_changes = updated.configuration_changes;
  GAME.stats = updated.stats;  // the modal's charts, token stacks, and usage columns all read off this
  // pull the fresh trace index too, so new turns and trace lines show on the timeline as they happen
  await refreshEvaluation(); rebuildTrace();
  el("slider").max = steps.length - 1;
  renderMasthead(); renderGameControl(); renderRail(); renderEvalCta(); renderEvalBar(); renderSynthesis(); renderTimeline();
  renderDriftNotice();
  if (statsOpen()) renderStats();
  show(following ? steps.length - 1 : index);
}

/* ================= INIT ================= */
buildBoard(); buildContinentLegend();
renderMasthead(); renderGameControl(); renderModeToggle(); renderRail(); renderEvalCta(); renderEvalBar(); renderSynthesis(); renderTimeline();
renderForkPanel();
renderDriftNotice();
show(LIVE ? steps.length - 1 : 0);
if (LIVE) setInterval(refreshFromJournal, 1000);
if (EVAL.running) pollEvaluation();  // a run already in flight (started before this page load)
if (location.hash === "#stats") openStats();  // deep link (kept from when stats lived only in the classic view)
