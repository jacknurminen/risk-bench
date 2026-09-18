/* Map input and a turn card on the existing reader; all drafts stay local until committed. */
(() => {
  const url = window.BOOT.urls.human;
  if (!url) return;
  const card = el("humanTurn"), control = el("humanControl"), side = el("humanSide");
  const inlinePosition = document.createComment("Human controls below the map on smaller screens");
  card.before(inlinePosition);
  const sideLayout = window.matchMedia("(min-width: 1280px)");
  function positionControls() {
    document.body.classList.toggle("humanControlsSide", sideLayout.matches);
    side.hidden = !sideLayout.matches;
    if (sideLayout.matches) side.append(card);
    else inlinePosition.after(card);
  }
  positionControls();
  sideLayout.addEventListener("change", positionControls);
  const names = {place_armies: "Place armies", attack: "Attack", fortify: "Fortify", trade_cards: "Trade cards",
    end_attack_phase: "Finish attacking", skip_fortify: "Skip fortification"};
  const option = (value, text) => { const node = document.createElement("option"); node.value = value; node.textContent = text; return node; };
  const cardLabel = c => c.territory ? `${c.design} (${placeName(c.territory)})` : "wild";
  let draft = null, lastDraft = null, submittedId = null, submitting = false, polling = false, connected = true;
  const badges = {};
  const route = document.createElementNS(SVG_NS, "g");
  route.setAttribute("class", "humanRoute");
  route.setAttribute("aria-hidden", "true");
  route.innerHTML = `<defs><marker id="humanArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>
    <path class="humanRouteHalo"/><path class="humanRouteLine" marker-end="url(#humanArrow)"/>`;
  Object.values(territoryShapes)[0].path.parentNode.append(route);
  function paintRoute(active) {
    const pair = active && draft.pair;
    route.style.display = pair ? "" : "none";
    if (!pair) return;
    const source = territoryShapes[draft.source].label, target = territoryShapes[draft.target].label;
    const x1 = +source.getAttribute("x"), y1 = +source.getAttribute("y");
    const x2 = +target.getAttribute("x"), y2 = +target.getAttribute("y");
    const length = Math.hypot(x2 - x1, y2 - y1), ux = (x2 - x1) / length, uy = (y2 - y1) / length;
    // Lift the curve away from the army counts and stop before the destination label.
    let path = `M ${x1 + ux * 14} ${y1 + uy * 14} Q ${(x1+x2)/2 + uy*20} ${(y1+y2)/2 - ux*20} ${x2-ux*14} ${y2-uy*14}`;
    // The Alaska–Kamchatka connection crosses the Pacific edge of the map.
    if (Math.abs(x2 - x1) > boardFrame.width / 2) {
      const svg = document.querySelector("#board svg");
      const toLocal = route.parentNode.getCTM().inverse().multiply(svg.getCTM());
      const left = new DOMPoint(boardFrame.x, 0).matrixTransform(toLocal).x;
      const right = new DOMPoint(boardFrame.x + boardFrame.width, 0).matrixTransform(toLocal).x;
      const direction = x1 < x2 ? -1 : 1;
      const exit = direction < 0 ? left : right, entry = direction < 0 ? right : left;
      path = `M ${x1 + direction*14} ${y1} L ${exit} ${y1} M ${entry} ${y2} L ${x2 - direction*14} ${y2}`;
    }
    route.querySelectorAll(":scope > path").forEach(line => line.setAttribute("d", path));
    route.classList.toggle("fortify", draft.mode === "fortify");
    route.parentNode.append(route);
  }
  // The reader and mailbox poll independently. Do not accept map input until
  // the displayed live snapshot actually matches the worker's decision board.
  function onLiveBoard() {
    if (!draft || index !== steps.length - 1 || timer) return false;
    const view = draft.request.view, step = steps[index];
    return step.turn === view.turn_number && step.phase === view.phase
      && Object.entries(view.owner).every(([t, owner]) => GAME.players[step.board[t][0]].name === owner
        && (view.phase === "setup_placement" && owner !== view.player_id || step.board[t][1] === view.armies[t]));
  }
  const editable = () => !!draft && connected && !submitting && gameRunning && onLiveBoard();
  control.onclick = () => {
    if (sideLayout.matches) side.scrollTo({top: 0, behavior: "smooth"});
    else {
      window.readerLayout?.focusMap(false);
      card.scrollIntoView({behavior: "smooth", block: "nearest"});
    }
  };
  el("humanGoLive").onclick = async () => { stopPlaying(); await refreshFromJournal(); show(steps.length - 1); render(); };

  function paintBoard() {
    const active = editable(), preview = active ? draft.preview : {};
    const sources = new Set(active ? draft.pairs.map(a => a.from_territory) : []);
    const targets = new Set(active ? draft.pairs.filter(a => a.from_territory === draft.source).map(a => a.to_territory) : []);
    for (const [territory, shape] of Object.entries(territoryShapes)) {
      const ownedPlacement = active && draft.mode === "place_armies" && draft.placements.some(a => a.territory === territory);
      const enabled = (ownedPlacement && draft.remaining > 0) || sources.has(territory) || targets.has(territory);
      const selectablePhase = active && ["place_armies", "attack", "fortify"].includes(draft.mode);
      shape.path.classList.toggle("humanUnavailable", selectablePhase && !enabled && ![draft.source, draft.target].includes(territory));
      shape.path.classList.toggle("humanEligible", enabled);
      shape.path.classList.toggle("humanTarget", targets.has(territory));
      shape.path.classList.toggle("humanSelected", active && [draft.source, draft.target].includes(territory));
      if (enabled) {
        shape.path.setAttribute("role", "button"); shape.path.setAttribute("tabindex", "0");
        shape.path.setAttribute("aria-label", `${ownedPlacement ? "Add one army to" : "Select"} ${placeName(territory)}`);
      } else { shape.path.removeAttribute("role"); shape.path.removeAttribute("tabindex"); shape.path.removeAttribute("aria-label"); }
      const delta = preview[territory] || 0;
      badges[territory].textContent = delta ? `${delta > 0 ? "+" : ""}${delta}` : "";
    }
    paintRoute(active);
  }
  for (const [territory, shape] of Object.entries(territoryShapes)) {
    const badge = document.createElementNS(SVG_NS, "text");
    badge.setAttribute("x", Number(shape.label.getAttribute("x")) + 11);
    badge.setAttribute("y", Number(shape.label.getAttribute("y")) - 7);
    badge.setAttribute("class", "humanBadge"); shape.path.parentNode.append(badge); badges[territory] = badge;
    const select = () => {
      if (!editable() || document.body.classList.contains("exporting")) return;
      if (draft.select(territory)) { el("humanError").textContent = ""; renderPlan(); }
    };
    shape.path.addEventListener("click", select);
    shape.path.addEventListener("keydown", event => {
      if (["Enter", " "].includes(event.key) && editable()) { event.preventDefault(); select(); }
    });
  }
  document.addEventListener("reader:board-change", () => render());
  document.addEventListener("reader:game-status", () => render());

  function renderAllocations() {
    el("humanProgress").textContent = `${draft.total} of ${draft.available} armies placed · ${draft.remaining} remaining`;
    el("humanUndo").disabled = !draft.history.length;
    el("humanClear").disabled = !draft.total;
    el("humanAddOne").disabled = el("humanAddRemaining").disabled = draft.remaining === 0;
    const rows = Object.entries(draft.allocations).map(([territory, count]) => {
      const row = document.createElement("div"); row.className = "humanAllocation";
      const name = document.createElement("span"); name.textContent = placeName(territory);
      const input = document.createElement("input"); input.type = "number"; input.min = 0;
      input.max = count + draft.remaining; input.value = count; input.setAttribute("aria-label", `Planned armies in ${placeName(territory)}`);
      input.onchange = () => { draft.allocate(territory, Number(input.value)); renderPlan(); };
      const minus = document.createElement("button"), plus = document.createElement("button");
      for (const [button, delta, label] of [[minus, -1, "Remove one army from"], [plus, 1, "Add one army to"]]) {
        button.type = "button"; button.className = "btn"; button.textContent = delta < 0 ? "−" : "+";
        button.setAttribute("aria-label", `${label} ${placeName(territory)} plan`);
        button.onclick = () => { draft.add(territory, delta); renderPlan(); };
      }
      plus.disabled = draft.remaining === 0; row.append(name, minus, input, plus); return row;
    });
    el("humanAllocations").replaceChildren(...rows);
  }
  function renderPair() {
    const sources = [...new Set(draft.pairs.map(a => a.from_territory))];
    el("humanSource").replaceChildren(option("", "Select a source on the map"), ...sources.map(t => option(t, placeName(t))));
    el("humanSource").value = draft.source;
    const targets = draft.pairs.filter(a => a.from_territory === draft.source).map(a => a.to_territory);
    el("humanTarget").replaceChildren(option("", "Select a highlighted target"), ...targets.map(t => option(t, placeName(t))));
    el("humanTarget").value = draft.target; el("humanTarget").disabled = !draft.source;
    const pair = draft.pair, armies = draft.request.view.armies;
    el("humanPairSummary").textContent = pair ? `${placeName(draft.source)} (${armies[draft.source]} armies) → `
      + `${placeName(draft.target)} (${armies[draft.target]} armies)` : draft.source ? "Now select a highlighted destination." : "Select one of your highlighted territories.";
    if (pair && draft.mode === "fortify") el("humanPairSummary").textContent +=
      ` · After move: ${armies[draft.source] - draft.count} → ${armies[draft.target] + draft.count} armies`;
    el("humanAttack").hidden = draft.mode !== "attack" || !pair;
    el("humanFortify").hidden = draft.mode !== "fortify" || !pair;
    if (pair && draft.mode === "attack") {
      el("humanDice").max = pair.attacker_dice;
      // Preserve a chosen die count for this pair, bounded by its current armies.
      el("humanDice").value = Math.min(Number(el("humanDice").value) || pair.attacker_dice, pair.attacker_dice);
    }
    if (pair && draft.mode === "fortify") for (const id of ["humanMoveSlider", "humanMoveCount"]) {
      el(id).max = pair.count; el(id).value = draft.count;
    }
  }
  function renderGuidance() {
    const mode = draft.mode;
    el("humanHint").textContent = mode === "place_armies" ? (draft.remaining
      ? `Place ${draft.remaining} more ${draft.remaining === 1 ? "army" : "armies"}.` : "All armies placed. Review and commit your plan.")
      : ["attack", "fortify"].includes(mode) ? (!draft.source ? `Choose a territory to ${mode === "attack" ? "attack" : "move armies"} from.`
        : !draft.target ? `Choose ${mode === "attack" ? "an enemy target" : "a friendly destination"}.`
        : mode === "attack" ? "Ready to attack. Choose how many times to roll." : `Move ${draft.count} ${draft.count === 1 ? "army" : "armies"}. Review and commit.`)
      : mode === "trade_cards" ? "Choose a card set to trade."
      : mode === "notes" ? `Record what you learned from turn ${draft.request.finished_turn}.`
      : mode === "negotiation" ? "Your diplomacy response." : "No moves available. Finish this phase.";
  }
  function renderPlan() {
    if (!draft) return;
    const mode = draft.mode;
    renderGuidance();
    el("humanHelp").textContent = mode === "notes" ? "Update your notebook before play continues. These notes are private to you and saved in the research journal. Clearing the text clears your notebook."
      : mode === "negotiation" ? `It is ${draft.request.view.current_player}’s turn. Speak or stay silent to continue.`
      : mode === "place_armies" ? "Click your territories to plan one army per click. Commit once all reinforcements are allocated."
      : mode === "attack" ? "Click your source and an enemy target, then confirm an attack. Each result updates the board."
      : mode === "fortify" ? "Click a source and a friendly destination, then choose how many armies to move."
      : mode === "trade_cards" ? "Choose a card set to trade. You will allocate the resulting reinforcements in a fresh plan."
      : "Choose how to finish this phase.";
    el("humanAttack").hidden = mode !== "attack" || !draft.pair;
    el("humanFortify").hidden = mode !== "fortify" || !draft.pair;
    el("humanPlacement").hidden = mode !== "place_armies";
    el("humanPair").hidden = !["attack", "fortify"].includes(mode);
    el("humanTradeLabel").hidden = mode !== "trade_cards";
    el("humanNotesLabel").hidden = mode !== "notes";
    el("humanNotes").disabled = mode !== "notes";
    el("humanSkipNotes").hidden = mode !== "notes";
    if (mode === "place_armies") renderAllocations();
    if (["attack", "fortify"].includes(mode)) renderPair();
    const ending = draft.actions.find(a => a.kind === (draft.request.view.phase === "fortify" ? "skip_fortify" : "end_attack_phase"));
    el("humanEnd").hidden = !ending || mode === ending.kind;
    el("humanEnd").textContent = ending ? names[ending.kind] : "";
    el("humanRepeat").hidden = mode !== "attack";
    el("humanRepeat").disabled = !draft.ready;
    el("humanSubmit").disabled = !draft.ready;
    el("humanSubmit").textContent = submitting ? "Submitting…" : mode === "place_armies" ? `Place ${draft.available} armies`
      : mode === "attack" ? "Roll once" : mode === "fortify" ? "Move armies & end turn"
      : mode === "notes" ? "Save notes & continue"
      : mode === "negotiation" ? (el("humanMessage").value.trim() ? "Send message" : "Say nothing") : names[mode];
    for (const id of ["humanPlacement", "humanAttack", "humanFortify", "humanTradeLabel"]) {
      const section = el(id);
      section.querySelectorAll("input, select").forEach(input => input.disabled = section.hidden);
    }
    paintBoard();
  }
  function render() {
    humanPending = !!draft && !submitting;
    renderGameControl();
    document.body.classList.toggle("humanTurnActive", !!draft);
    el("humanWaiting").hidden = !!draft || submitting;
    el("humanWaiting").textContent = GAME.result ? "Game finished. Explore the timeline and analysis."
      : gameRunning ? "Agents are playing. Your controls will appear here when needed."
      : "Game paused. Resume the game to continue your turn.";
    card.hidden = !draft && !submitting; control.style.display = draft ? "" : "none";
    if (!draft) { paintBoard(); return; }
    el("humanFields").disabled = !editable();
    if (!connected) el("humanHint").textContent = "Connection lost. Retrying… Your draft is saved.";
    else if (!gameRunning) el("humanHint").textContent = "Game paused. Resume to continue your turn.";
    else if (submitting) el("humanHint").textContent = "Submitting your move…";
    else if (!onLiveBoard()) el("humanHint").textContent = "Return to the live board to continue.";
    else renderGuidance();
    el("humanLiveNotice").hidden = onLiveBoard() || !gameRunning;
    paintBoard();
  }
  function populate() {
    const view = draft.request.view, mode = draft.mode;
    el("humanTitle").textContent = mode === "notes" ? "Your private notebook" : mode === "negotiation" ? "Your diplomacy response" : "Your turn";
    control.textContent = el("humanTitle").textContent;
    el("humanContext").textContent = mode === "notes" ? `${view.player_id} · after turn ${draft.request.finished_turn}`
      : `${view.player_id} · turn ${view.turn_number} · ${pretty(view.phase)}`;
    el("humanMission").textContent = view.mission;
    el("humanCards").textContent = "Cards: " + (view.own_cards.map(cardLabel).join(", ") || "none");
    el("humanMessage").value = ""; el("humanThought").value = ""; el("humanError").textContent = "";
    el("humanNotes").value = view.notes ?? "";
    el("humanActions").hidden = ["negotiation", "notes"].includes(mode);
    el("humanMessageLabel").hidden = el("humanReasoning").hidden = mode === "notes";
    const mainModes = draft.modes.filter(m => !["end_attack_phase", "skip_fortify"].includes(m));
    el("humanKind").replaceChildren(...(mainModes.length ? mainModes : draft.modes).map(m => option(m, names[m])));
    el("humanKind").value = mode; el("humanModeLabel").hidden = mainModes.length <= 1;
    el("humanPlaceTerritory").replaceChildren(...draft.placements.map(a => option(a.territory, placeName(a.territory))));
    el("humanTrade").replaceChildren(...draft.actions.filter(a => a.kind === "trade_cards")
      .map(a => option(a.action_index, a.cards.map(cardLabel).join(" + "))));
    el("humanTrade").value = draft.tradeIndex;
    el("humanRecipient").replaceChildren(option("", "Everyone"));
    if (view.private_messages_allowed) view.players.filter(p => !p.is_eliminated && p.player_id !== view.player_id)
      .forEach(p => el("humanRecipient").append(option(p.player_id, p.player_id)));
    el("humanRecipientLabel").hidden = mode === "notes" || !view.private_messages_allowed;
    el("humanDice").value = draft.pair?.attacker_dice || 3;
    el("humanLeave").value = 1; el("humanRolls").value = 3;
    // Only new battle outcomes, rather than the entire turn's history on every roll.
    const oldEvents = lastDraft?.request.view.events || [];
    const events = view.events.slice(oldEvents.every((e, i) => view.events[i] === e) ? oldEvents.length : 0);
    const results = events.filter(e => /attacked|conquered|eliminated/i.test(e));
    if (results.length) el("humanResult").replaceChildren(...results.slice(-8).map(text => {
      const line = document.createElement("p"); line.textContent = text; return line;
    }));
    else if (lastDraft?.request.view.turn_number !== view.turn_number) el("humanResult").replaceChildren();
    renderPlan(); render();
  }
  async function refresh() {
    if (polling || submitting) return;
    if (GAME.result) { draft = null; render(); return; }
    polling = true;
    try {
      const response = await fetch(url, {cache: "no-store"}), body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not load human controls.");
      if (submitting) return;
      connected = true; gameRunning = body.running; renderGameControl();
      const request = body.request?.request_id !== submittedId ? body.request : null;
      if (request && request.request_id !== draft?.request.request_id) {
        if (draft) lastDraft = draft;
        draft = new HumanTurnDraft(request, lastDraft); populate();
      } else if (!request) { if (draft) lastDraft = draft; draft = null; }
      render();
    } catch (error) { connected = false; el("humanError").textContent = error.message; render(); }
    finally { polling = false; }
  }
  async function submit(action = null, repeat = false) {
    if (!editable() || (!action && !draft.ready)) return;
    if (!action && !el("humanForm").reportValidity()) return;
    const payload = draft.mode === "notes" ? {request_id: draft.request.request_id, notes: el("humanNotes").value, ...action}
      : {request_id: draft.request.request_id, message: el("humanMessage").value,
      recipient: el("humanRecipient").value || null, thought: el("humanThought").value, ...(action || draft.response())};
    if (!action && draft.mode === "attack") Object.assign(payload, {attacker_dice: Number(el("humanDice").value),
      max_attacks: repeat ? Number(el("humanRolls").value) : 1, armies_to_leave_behind: Number(el("humanLeave").value)});
    submitting = true; el("humanError").textContent = ""; renderPlan(); render();
    try {
      const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the values in your plan.");
      submittedId = payload.request_id; lastDraft = draft; draft = null;
    } catch (error) { el("humanError").textContent = error.message; }
    finally { submitting = false; render(); await refresh(); }
  }
  el("humanKind").onchange = () => { draft.setMode(el("humanKind").value); renderPlan(); };
  el("humanSource").onchange = () => { draft.selectSource(el("humanSource").value); renderPlan(); };
  el("humanTarget").onchange = () => { draft.select(el("humanTarget").value); renderPlan(); };
  el("humanTrade").onchange = () => { draft.tradeIndex = Number(el("humanTrade").value); };
  el("humanUndo").onclick = () => { draft.undo(); renderPlan(); };
  el("humanClear").onclick = () => { draft.clear(); renderPlan(); };
  el("humanAddOne").onclick = () => { draft.add(el("humanPlaceTerritory").value); renderPlan(); };
  el("humanAddRemaining").onclick = () => { draft.add(el("humanPlaceTerritory").value, draft.remaining); renderPlan(); };
  for (const id of ["humanMoveCount", "humanMoveSlider"]) el(id).oninput = () => {
    if (draft.setCount(Number(el(id).value))) renderPlan();
  };
  el("humanMoveMinus").onclick = () => { draft.setCount(draft.count - 1); renderPlan(); };
  el("humanMovePlus").onclick = () => { draft.setCount(draft.count + 1); renderPlan(); };
  el("humanMessage").oninput = () => renderPlan();
  el("humanForm").onsubmit = event => { event.preventDefault(); submit(); };
  el("humanSkipNotes").onclick = () => submit({notes: null});
  el("humanRepeat").onclick = () => { if (draft.ready) submit(null, true); };
  el("humanEnd").onclick = () => {
    const action = draft.actions.find(a => a.kind === (draft.request.view.phase === "fortify" ? "skip_fortify" : "end_attack_phase"));
    if (action) submit({action_index: action.action_index});
  };
  async function poll() { await refresh(); if (!GAME.result) setTimeout(poll, 1000); }
  poll();
})();
