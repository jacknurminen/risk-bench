/* ================= BOARD (ported, restyled) ================= */
const SVG_NS = "http://www.w3.org/2000/svg";
const territoryShapes = {};
const boardFrame = {};
function renderBoardStatus() {
  const step = steps[index];
  const location = index === steps.length - 1 && !timer ? readerStatus() : "Replay";
  el("status").textContent = `step ${index}/${steps.length - 1} · turn ${step.turn} · ${pretty(step.phase)} · ${location}`;
}

/* Zoom the SVG itself so text stays sharp. Dragging never submits a territory click. */
function setupMapZoom(svg) {
  const original = {};
  for (const key of ["x", "y", "width", "height"]) original[key] = svg.viewBox.baseVal[key];
  Object.assign(boardFrame, original);
  let zoom = 1, x = original.x, y = original.y, drag = null, dragged = false;
  svg.setAttribute("tabindex", "0");
  svg.setAttribute("aria-label", "Risk map. Use zoom buttons to enlarge; drag or use arrow keys to pan when zoomed.");
  function apply() {
    const width = original.width / zoom, height = original.height / zoom;
    x = Math.max(original.x, Math.min(original.x + original.width - width, x));
    y = Math.max(original.y, Math.min(original.y + original.height - height, y));
    svg.setAttribute("viewBox", `${x} ${y} ${width} ${height}`);
    svg.classList.toggle("zoomed", zoom > 1);
    el("mapZoomReset").textContent = `${Math.round(zoom * 100)}%`;
    el("mapZoomOut").disabled = zoom <= 1;
    el("mapZoomIn").disabled = zoom >= 3;
    el("boardHint").textContent = zoom > 1 ? "Drag to pan · click % to reset" : "";
  }
  function change(next) {
    const old = zoom;
    zoom = Math.max(1, Math.min(3, next));
    x += original.width / old / 2 - original.width / zoom / 2;
    y += original.height / old / 2 - original.height / zoom / 2;
    apply();
  }
  el("mapZoomIn").onclick = () => change(zoom + .5);
  el("mapZoomOut").onclick = () => change(zoom - .5);
  el("mapZoomReset").onclick = () => change(1);
  svg.addEventListener("keydown", event => {
    const direction = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
    if (zoom <= 1 || !direction) return;
    event.preventDefault(); event.stopPropagation();
    x += direction[0] * original.width / zoom / 8;
    y += direction[1] * original.height / zoom / 8;
    apply();
  });
  svg.addEventListener("pointerdown", event => {
    dragged = false;
    if (zoom <= 1 || event.button !== 0 || document.body.classList.contains("exporting")) return;
    const matrix = svg.getScreenCTM().inverse();
    drag = {id: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      point: new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix), matrix, x, y};
  });
  svg.addEventListener("pointermove", event => {
    if (!drag || drag.id !== event.pointerId) return;
    if (!dragged && Math.hypot(event.clientX - drag.clientX, event.clientY - drag.clientY) < 5) return;
    dragged = true;
    svg.setPointerCapture(event.pointerId);
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(drag.matrix);
    x = drag.x + drag.point.x - point.x; y = drag.y + drag.point.y - point.y;
    apply();
  });
  const endDrag = event => {
    if (drag?.id !== event.pointerId) return;
    drag = null;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("click", event => {
    if (dragged) { event.preventDefault(); event.stopImmediatePropagation(); dragged = false; }
  }, true);
  apply();
}

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
    nameLabel.setAttribute("x", cx); nameLabel.setAttribute("y", cy + 11); nameLabel.setAttribute("class", "tname");
    const words = placeName(territory).split(" ");
    const splits = words.slice(1).map((_, i) => [words.slice(0, i + 1).join(" "), words.slice(i + 1).join(" ")]);
    const lines = words.length > 1 && placeName(territory).length > 12
      ? splits.sort((a, b) => Math.max(...a.map(t => t.length)) - Math.max(...b.map(t => t.length)))[0] : [placeName(territory)];
    lines.forEach((text, i) => {
      const line = document.createElementNS(SVG_NS, "tspan");
      line.setAttribute("x", cx); line.setAttribute("dy", i ? 8 : 0); line.textContent = text;
      nameLabel.append(line);
    });
    path.parentNode.appendChild(nameLabel);
    const tooltip = document.createElementNS(SVG_NS, "title"); path.appendChild(tooltip);
    territoryShapes[territory] = { path, label, nameLabel, tooltip };
  }
  setupMapZoom(svg);
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
document.addEventListener("click", e => {
  const link = e.target.closest(".tlink");
  if (link) { flashPlace(link.dataset.place); return; }
  const card = e.target.closest("[data-mission-continents]");
  if (card) card.dataset.missionContinents.split(" ").forEach(flashPlace);
});

function show(newIndex) {
  index = Math.max(0, Math.min(steps.length - 1, newIndex));
  const step = steps[index];
  for (const [territory, [seat, armies]] of Object.entries(step.board)) {
    const shape = territoryShapes[territory]; if (!shape) continue;
    shape.path.style.fill = COLORS[seat]; shape.path.style.fillOpacity = "0.78";
    shape.label.textContent = armies;
    shape.tooltip.textContent = `${placeName(territory)} (${placeName(CONTINENT_OF[territory])}) — ${GAME.players[seat].name}, ${armies} armies`;
  }
  document.dispatchEvent(new CustomEvent("reader:board-change"));
  updateContinentLegend(step);
  renderPlayerHands(step);
  el("statStrip").innerHTML = step.players.map((s, seat) =>
    `<span style="color:${COLORS[seat]}${s.eliminated ? ";opacity:.45;text-decoration:line-through" : ""}">${tagOf(GAME.players[seat].name)} ${s.territories}▪ ${s.armies}⚔ ${s.cards}◈ +${s.income}</span>`).join("");
  el("slider").value = index;
  renderBoardStatus();
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
