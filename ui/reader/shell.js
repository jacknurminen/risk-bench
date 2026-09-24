/* ================= MASTHEAD + MODE ================= */
function renderMasthead() {
  const status = readerStatus();
  el("mastStatus").innerHTML = `<span class="dot"></span>${status}`;
  el("mastWinner").innerHTML = GAME.result && GAME.result.winner
    ? `winner <span style="color:${colorOf(GAME.result.winner)};font-weight:600">${tagOf(GAME.result.winner)}</span>` : "";
}
function renderModeToggle() {
  el("modeToggle").innerHTML = [["read", "Timeline"], ["research", "Commitments & claims"], ["compare", "Compare"]].map(([k, l]) =>
    `<button class="${state.mode === k ? "on" : ""}" onclick="setMode('${k}')">${l}</button>`).join("");
}
function setMode(m) { state.mode = m; renderModeToggle(); renderTimeline(); }

/* pause/resume the running game worker (only meaningful while the game is unfinished) */
function renderGameControl() {
  renderMasthead();
  renderBoardStatus();
  const btn = el("gameControl");
  if (!LIVE || GAME.result) { btn.style.display = "none"; return; }
  btn.style.display = "";
  btn.textContent = gameRunning ? "⏸ pause game" : "▶ resume game";
  let reason = el("pauseReason");
  if (!reason) {
    reason = document.createElement("p");
    reason.id = "pauseReason"; reason.className = "researchCaution";
    el("timeline").before(reason);
  }
  reason.hidden = gameRunning || !GAME.pause_error;
  reason.textContent = GAME.pause_error || "";
}
el("gameControl").onclick = async () => {
  const action = gameRunning ? "pause" : "resume";
  const res = await fetch(`${POLL_URL}/${action}`, { method: "POST" });
  if (res.ok) { gameRunning = action === "resume"; renderGameControl(); document.dispatchEvent(new CustomEvent("reader:game-status")); toast("game " + (gameRunning ? "resumed" : "paused")); return; }
  // a refused resume carries the worker's own words; without them the game just silently never moves
  const detail = await res.json().then(body => body.detail, () => "");
  if (res.status === 409 && !detail) { gameRunning = action === "resume"; renderGameControl(); document.dispatchEvent(new CustomEvent("reader:game-status")); return; }
  toast(detail ? `could not ${action} the game — ${detail}` : `could not ${action} the game`);
};

/* the full private-note history for one player (every end-of-turn version), like the classic view */
function openNotes(name) {
  const versions = GAME.notes.filter(n => n.player === name);
  el("notesTitle").innerHTML = `📝 <span style="color:${colorOf(name)}">${tagOf(name)}</span> — private notes`;
  el("notesBody").innerHTML = versions.length
    ? [...versions].reverse().map(n =>
        `<div class="noteVersion"><div class="noteMeta">end of turn ${n.turn}</div><div class="noteText">${richText(n.text)}</div></div>`).join("")
    : `<div class="empty">This player kept no notes.</div>`;
  el("notesModal").classList.add("open");
}
function closeNotes() { el("notesModal").classList.remove("open"); }

/* ================= EVALUATION REFRESH ================= */
async function refreshEvaluation() {
  const url = selectedVersion ? `${EVAL_POLL_URL}?version=${encodeURIComponent(selectedVersion)}` : EVAL_POLL_URL;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return;
  EVAL = await res.json();
  annotators().forEach(a => {
    if (knownAnnotators.has(a.index)) return;
    knownAnnotators.add(a.index);
    state.filters.annotators.add(a.index);
  });
}

/* ================= PLAYBACK WIRING (ported) ================= */
function stopPlaying() { clearInterval(timer); timer = null; el("play").textContent = "▶ play"; }
function turnBoundary(direction) { const turn = steps[index].turn; let i = index;
  while (i + direction >= 0 && i + direction < steps.length && steps[i = i + direction].turn === turn); return i; }
el("first").onclick = () => show(0);
el("last").onclick = () => show(steps.length - 1);
el("prev").onclick = () => show(index - 1);
el("next").onclick = () => show(index + 1);
el("prevTurn").onclick = () => show(turnBoundary(-1));
el("nextTurn").onclick = () => show(turnBoundary(1));
el("slider").max = steps.length - 1;
el("slider").oninput = e => show(Number(e.target.value));
el("play").onclick = () => { if (timer) { stopPlaying(); return; } el("play").textContent = "⏸ pause";
  timer = setInterval(() => { if (index >= steps.length - 1) stopPlaying(); else show(index + 1); }, 250); };
document.addEventListener("keydown", e => {
  if (e.target.closest("input, textarea, select, [contenteditable=true]")) return;
  if (e.key === "Escape" && statsOpen()) { closeStats(); return; }
  if (e.key === "Escape" && el("notesModal").classList.contains("open")) { closeNotes(); return; }
  if (state.drawer) { if (e.key === "Escape") { state.drawer = null; renderPanels(); } return; }
  if (e.key === "ArrowLeft") show(index - 1);
  if (e.key === "ArrowRight") show(index + 1);
});

/* ================= TOAST ================= */
let toastTimer = null;
function toast(message) { const t = el("toast"); t.textContent = message; t.style.display = "block";
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.style.display = "none", 2600); }
