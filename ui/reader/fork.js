/* Forking is a plain copy. Every committed execution boundary between two trace entries is a
   seam: hovering (or tabbing to) it offers one button that copies the game up to exactly there
   into a new, paused branch. The branch inherits the whole history, notes, pending work and
   random streams, so the only thing left to decide is what — if anything — to change before
   resuming it. */

let cursorNextOperation = null, indexedCursors = null;
function forkNextOperation(cursorId) {
  const cursors = GAME.cursors || [];
  if (indexedCursors !== cursors) {
    indexedCursors = cursors;
    cursorNextOperation = new Map();
    cursors.forEach((c, i) => {
      const describe = entry => entry.next_description || entry.next_operation;
      cursorNextOperation.set(c.after, describe(c));
      cursorNextOperation.set(c.before, i ? describe(cursors[i - 1]) : "Deal the board and open the game");
    });
  }
  return cursorNextOperation.get(cursorId) || "";
}

function forkSeparator(cursorId) {
  if (!POLL_URL || !cursorId) return "";
  const next = forkNextOperation(cursorId);
  if (!next) return "";
  return `<div class="forkSep"><button class="forkBtn" type="button" onclick="event.stopPropagation();createFork('${cursorId}',this)"
    title="Copy this game up to here into a paused branch. Next: ${escapeHtml(next)}">Fork from here</button></div>`;
}

async function createFork(cursor, button) {
  button.disabled = true;
  button.textContent = "Copying…";
  try {
    const response = await fetch(POLL_URL + "/fork", {
      method: "POST", headers: {"Content-Type": "application/json"},
      // The branch starts paused: the parent keeps running, and the copy waits to be set up.
      body: JSON.stringify({cursor, start: false}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not fork this game");
    window.location.href = result.url;
  } catch (error) {
    button.disabled = false;
    button.textContent = "Fork from here";
    toast(error.message);
  }
}

/* A game that was resumed under edited code keeps playing — refusing would strand long games and
   every fork of an older one — but it must never look like one uninterrupted run. */
function renderDriftNotice() {
  const notice = el("driftNotice");
  const changes = GAME.configuration_changes || [];
  notice.hidden = !changes.length;
  if (!notice.hidden)
    notice.innerHTML = `<strong>Continued under changed code.</strong> This game did not run end to end
      on one version, so treat it as spanning more than one when comparing it with others.
      <ul>${changes.map(change => `<li>${escapeHtml(change)}</li>`).join("")}</ul>`;
}
