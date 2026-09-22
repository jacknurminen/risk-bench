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

/* ================= PAUSED BRANCH: WHAT EACH PLAYER KNOWS ================= */
/* The intervention is information, not instruction: the text below is rendered into the player's
   next prompt in the same register as the board and the chat, so a comparison between two
   branches measures the information, not a change of tone from the experimenter. */
function renderForkPanel() {
  const panel = el("forkPanel");
  const ancestry = GAME.ancestry || {};
  // Only a paused branch: the API refuses a context edit while the worker is playing, and the
  // edit would in any case arrive after whatever request is already in flight.
  const editable = Boolean(POLL_URL && ancestry.parent_game_id && !GAME.result && !gameRunning);
  panel.hidden = !editable;
  // live polling calls this every second. Rebuilding would discard whatever is half-written, and
  // focus alone is not enough to tell: someone types, clicks the board to check it, and comes back.
  if (!editable || panel.contains(document.activeElement) || forkContextBoxes().some(box => box.value.trim())) return;
  const stored = GAME.additional_context || {};
  panel.innerHTML = `<div class="forkHead"><span class="serif">Forked copy · paused</span>
      <span class="mono">from <a href="/games/${encodeURIComponent(ancestry.parent_game_id)}">${escapeHtml(ancestry.parent_game_id)}</a>
      at cursor ${escapeHtml(ancestry.parent_cursor || "")}</span></div>
    <p>This branch holds the parent's history, notes, pending work and random streams up to that point.
      What you add below joins what a player already knows and is rendered into every prompt they
      receive from here on; already generated responses are untouched. Leave it all empty for a plain copy.</p>
    <div class="forkPlayers">${GAME.players.map(p => `<label>
      <span class="mono" style="color:${colorOf(p.name)}">${escapeHtml(p.name)}</span>
      ${stored[p.name] ? `<div class="forkInherited"><span class="eyebrow">already knows</span>${escapeHtml(stored[p.name])}</div>` : ""}
      <textarea rows="3" data-player="${escapeHtml(p.name)}"
        placeholder="${stored[p.name] ? "add to what this player knows" : "e.g. what this player has learned about their opponents"}"></textarea>
      </label>`).join("")}</div>
    <div class="forkActions">
      <button class="btn" type="button" onclick="previewForkPrompts()">Preview next prompts</button>
      <button class="btn dark" type="button" onclick="saveForkContext()">Save &amp; resume game</button>
      <span class="mono" id="forkStatus"></span></div>
    <div id="forkPreview"></div>`;
}

const forkContextBoxes = () => [...el("forkPanel").querySelectorAll("textarea[data-player]")];

/* Additions, never replacements: a branch inherits a briefing and must not silently drop it. */
function forkContextAdditions({joined} = {}) {
  const stored = GAME.additional_context || {};
  const edits = {};
  forkContextBoxes().forEach(box => {
    const added = box.value.trim();
    if (!added) return;
    const player = box.dataset.player;
    edits[player] = joined ? [stored[player], added].filter(Boolean).join("\n\n") : added;
  });
  return edits;
}

async function previewForkPrompts() {
  el("forkStatus").textContent = "Rendering…";
  try {
    // the preview renders the whole section, so it needs the joined text rather than the addition
    const prompts = await postForkContext("/prompt-preview", forkContextAdditions({joined: true}));
    el("forkStatus").textContent = "";
    el("forkPreview").innerHTML = prompts.prompts.map(forkPromptPreview).join("");
  } catch (error) { el("forkStatus").textContent = error.message; }
}

function forkPromptPreview(prompt) {
  const when = prompt.is_next_request
    ? `the game's next request — ${prompt.request_kind}`
    : `their next ${prompt.request_kind} opportunity, rendered from this position`;
  return `<details class="forkPrompt"><summary><span class="mono" style="color:${colorOf(prompt.player)}">${escapeHtml(prompt.player)}</span>
      <span class="mono">${escapeHtml(when)}</span>${prompt.before === prompt.after ? "" : `<span class="forkChanged">changed</span>`}</summary>
    <div class="forkPromptPanes">
      <div><div class="eyebrow">now</div><pre>${escapeHtml(prompt.before)}</pre></div>
      <div><div class="eyebrow">after your edit</div><pre>${markInsertion(prompt.before, prompt.after)}</pre></div>
    </div>
    <details class="forkSystem"><summary>System prompt (unchanged)</summary><pre>${escapeHtml(prompt.system_prompt)}</pre></details>
  </details>`;
}

/* Highlight exactly what the edit adds: the span between the texts' common prefix and suffix. */
function markInsertion(before, after) {
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start++;
  let end = 0;
  while (end < before.length - start && end < after.length - start
    && before[before.length - 1 - end] === after[after.length - 1 - end]) end++;
  const inserted = after.slice(start, after.length - end);
  return escapeHtml(after.slice(0, start))
    + (inserted ? `<mark>${escapeHtml(inserted)}</mark>` : "")
    + escapeHtml(after.slice(after.length - end));
}

async function saveForkContext() {
  el("forkStatus").textContent = "Saving…";
  try {
    const additions = forkContextAdditions();
    await postForkContext("/context", additions, {append: true});
    GAME.additional_context = {...GAME.additional_context, ...forkContextAdditions({joined: true})};
    // the additions are committed, so the boxes are empty again and the panel may rebuild freely
    forkContextBoxes().forEach(box => box.value = "");
    el("forkStatus").textContent = "Saved. Starting the branch…";
    const started = await fetch(POLL_URL + "/resume", {method: "POST"});
    if (!started.ok) {
      const detail = await started.json().then(body => body.detail, () => "");
      throw new Error(detail ? `Context saved, but the branch could not start — ${detail}` : "Context saved, but the branch could not start");
    }
    el("forkStatus").textContent = "Running.";
    gameRunning = true;
    renderGameControl();
    renderForkPanel();
    document.dispatchEvent(new CustomEvent("reader:game-status"));
  } catch (error) { el("forkStatus").textContent = error.message; }
}

async function postForkContext(path, context, extra = {}) {
  const response = await fetch(POLL_URL + path, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({context, ...extra}),
  });
  if (response.status === 204) return {};
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "The branch could not be updated");
  return result;
}
