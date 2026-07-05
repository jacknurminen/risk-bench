/* ================= BOARD (ported, restyled) ================= */
const SVG_NS = "http://www.w3.org/2000/svg";
const territoryShapes = {};
function buildBoard() {
  const svg = document.querySelector("#board svg");
  for (const territory of Object.keys(steps[0].board)) {
    const path = svg.getElementById(territory);
    if (!path) continue;
    const box = path.getBBox();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", cx); label.setAttribute("y", cy); label.setAttribute("class", "acount");
    path.parentNode.appendChild(label);
    const nameLabel = document.createElementNS(SVG_NS, "text");
    nameLabel.setAttribute("x", cx); nameLabel.setAttribute("y", cy + 8); nameLabel.setAttribute("class", "tname");
    nameLabel.textContent = pretty(territory); path.parentNode.appendChild(nameLabel);
    const tooltip = document.createElementNS(SVG_NS, "title"); path.appendChild(tooltip);
    territoryShapes[territory] = { path, label, tooltip };
  }
}
function buildContinentLegend() {
  const container = el("continentLegend");
  GAME.continents.forEach((c, i) => {
    const row = document.createElement("div");
    row.innerHTML = `<span class="cdot" id="cl-${i}"></span><span class="tlink" data-place="${c.name}">${pretty(c.name)}</span> +${c.bonus}`;
    container.appendChild(row);
  });
}
function updateContinentLegend(step) {
  GAME.continents.forEach((c, i) => {
    const seats = new Set(c.territories.map(t => step.board[t][0]));
    const controller = seats.size === 1 ? [...seats][0] : null;
    const dot = el("cl-" + i);
    dot.style.background = controller === null ? "transparent" : COLORS[controller];
  });
}
function flashPlace(place) {
  for (const territory of CONTINENT_TERRITORIES[place] ?? [place]) {
    const shape = territoryShapes[territory]; if (!shape) continue;
    shape.path.classList.remove("flash"); void shape.path.getBoundingClientRect(); shape.path.classList.add("flash");
  }
}
document.addEventListener("click", e => { const link = e.target.closest(".tlink"); if (link) flashPlace(link.dataset.place); });

function show(newIndex) {
  index = Math.max(0, Math.min(steps.length - 1, newIndex));
  const step = steps[index];
  for (const [territory, [seat, armies]] of Object.entries(step.board)) {
    const shape = territoryShapes[territory]; if (!shape) continue;
    shape.path.style.fill = COLORS[seat]; shape.path.style.fillOpacity = "0.78";
    shape.label.textContent = armies;
    shape.tooltip.textContent = `${pretty(territory)} (${pretty(CONTINENT_OF[territory])}) — ${GAME.players[seat].name}, ${armies} armies`;
  }
  updateContinentLegend(step);
  el("statStrip").innerHTML = step.players.map((s, seat) =>
    `<span style="color:${COLORS[seat]}${s.eliminated ? ";opacity:.45;text-decoration:line-through" : ""}">${tagOf(GAME.players[seat].name)} ${s.territories}▪ ${s.armies}⚔ ${s.cards}◈ +${s.income}</span>`).join("");
  el("slider").value = index;
  const liveMark = LIVE && !GAME.result ? " · LIVE" : "";
  el("status").textContent = `step ${index}/${steps.length - 1} · turn ${step.turn} · ${pretty(step.phase)}${liveMark}`;
  el("boardTurnLabel").textContent = "Board · turn " + String(step.turn).padStart(2, "0");
  state.focusTurn = step.turn;
  document.querySelectorAll(".tcard").forEach(card => card.classList.toggle("focus", Number(card.dataset.turn) === step.turn));
  // update rail stat lines
  step.players.forEach((s, seat) => {
    const line = el("prstats-" + seat); if (!line) return;
    line.textContent = `${s.territories} terr · ${s.armies} armies · ${s.cards} cards · +${s.income}/turn`;
    el("pcard-" + seat).classList.toggle("acting", GAME.players[seat].name === step.current_player);
  });
}

/* focus a turn: move the board to that turn's end and scroll the card into view */
function focusTurn(turn) { show(turnStep(turn)); }

/* clicking a trace line winds the pinned board to that line's exact step; the line's own
   buttons (cite chip, bubble stack, +) keep their roles, so their clicks don't wind */
function lineClick(event, step) {
  if (event.target.closest("button")) return;
  stopPlaying();
  show(step);
}

