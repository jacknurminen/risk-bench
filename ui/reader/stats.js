/* Game measurements and API calls are separate from research interpretations. */
function openStats() { renderStats(); el("statsModal").classList.add("open"); }
function closeStats() { el("statsModal").classList.remove("open"); }
const statsOpen = () => el("statsModal").classList.contains("open");
function renderStats() {
  const series = GAME.stats.series;
  el("statsLegend").innerHTML = GAME.players.map(p => '<span class="key" style="color:' + colorOf(p.name) + '">' + escapeHtml(p.name) + '</span>').join("");
  el("charts").innerHTML = ["army_share","territories","income","cards"].map(key => {
    const max = Math.max(1,...series.players.flatMap(p => p[key] || []));
    const points = values => values.map((v,i) => (32 + i * 490 / Math.max(1,values.length-1)) + "," + (155-v/max*130)).join(" ");
    return '<figure class="chart"><figcaption>' + escapeHtml(pretty(key)) + '</figcaption>' +
      '<svg viewBox="0 0 550 180" role="img" aria-label="' + escapeHtml(pretty(key)) + ' by turn">' +
      '<text x="2" y="25" class="axis">' + max.toFixed(1) + '</text><text x="15" y="160" class="axis">0</text>' +
      series.players.map((p,i) => '<polyline points="' + points(p[key] || []) + '" stroke="' + COLORS[i] + '" stroke-width="2" fill="none"/>').join("") +
      '<text x="32" y="177" class="axis">Turn ' + (series.turns[0] ?? 0) + '</text><text x="470" y="177" class="axis">Turn ' + (series.turns.at(-1) ?? 0) + '</text></svg></figure>';
  }).join("");
  renderPlayerOutput();
  el("commentaryStats").hidden = true;
  const usage = Object.entries(GAME.stats.usage || {});
  el("tokenSection").hidden = !usage.length;
  el("tokenSection").innerHTML = '<h3>API usage</h3><div class="researchTable"><table class="statsTable"><thead><tr><th>Caller</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr></thead><tbody>' +
    usage.map(([name,u]) => '<tr><td>' + escapeHtml(name) + '</td><td>' +
      Number(u.input_tokens || 0).toLocaleString() + '</td><td>' + Number(u.output_tokens || 0).toLocaleString() +
      '</td><td>' + (u.cost_usd == null ? "Unknown" : "$" + u.cost_usd.toFixed(4)) + '</td></tr>').join("") + '</tbody></table></div>';
  el("tagFilterNote").textContent = "";
  el("statsTable").innerHTML = '<thead><tr><th>Player</th><th>Attacks</th><th>Conquests</th><th>Armies lost</th><th>Messages</th></tr></thead><tbody>' +
    GAME.players.map((p,i) => {const s=GAME.stats.per_player[i]; return '<tr><td>' + escapeHtml(p.name) + '</td><td>' +
      s.attacks + '</td><td>' + s.conquests + '</td><td>' + s.armies_lost + '</td><td>' + (s.public_messages+s.private_messages) + '</td></tr>';}).join("") + '</tbody>';
  if (!el("evalSheet").hidden) renderEvalStats();
}

// Use the game's recorded text, so these measurements also work in offline exports
// and are independent of evaluator annotations or the API trace being available.
function playerOutputStats(game) {
  const turnCount = new Set(game.steps.map(step => step.turn)).size;
  const rows = game.players.map(player => ({name: player.name, publicMessages: 0, privateMessages: 0,
    messages: 0, messageWords: 0, thoughts: 0, thoughtWords: 0}));
  const byName = new Map(rows.map(row => [row.name, row]));
  const wordCount = text => (text || "").trim().match(/\S+/gu)?.length || 0;
  for (const message of game.messages || []) {
    const row = byName.get(message.sender);
    if (!row) continue;
    row.messages++;
    row[message.visibility === "private" ? "privateMessages" : "publicMessages"]++;
    row.messageWords += wordCount(message.text);
  }
  for (const thought of game.thoughts || []) {
    const row = byName.get(thought.player);
    if (!row) continue;
    row.thoughts++;
    row.thoughtWords += wordCount(thought.text);
  }
  for (const row of rows) {
    row.messagesPerTurn = turnCount ? row.messages / turnCount : null;
    row.thoughtsPerTurn = turnCount ? row.thoughts / turnCount : null;
    row.wordsPerMessage = row.messages ? row.messageWords / row.messages : null;
    row.wordsPerThought = row.thoughts ? row.thoughtWords / row.thoughts : null;
  }
  return {rows, turnCount};
}

function outputNumber(value, decimals = 0) {
  return value == null ? "—" : value.toLocaleString("en-US", {maximumFractionDigits: decimals});
}

function outputBarChart(rows, key, title, unit, decimals = 0) {
  const max = Math.max(1, ...rows.map(row => row[key] ?? 0));
  return '<figure class="chart outputChart"><figcaption>' + escapeHtml(title) + '</figcaption>' +
    '<div class="outputBars">' + rows.map(row => {
      const value = row[key];
      const label = value == null ? "No entries" : outputNumber(value, decimals) + " " + unit;
      return '<div class="outputBarRow"><span class="outputPlayer" style="color:' + colorOf(row.name) + '">' +
        escapeHtml(row.name) + '</span><span class="outputValue">' + escapeHtml(label) + '</span>' +
        '<div class="outputTrack" aria-hidden="true"><span style="width:' + ((value ?? 0) / max * 100) +
        '%;background:' + colorOf(row.name) + '"></span></div></div>';
    }).join("") + '</div></figure>';
}

function renderPlayerOutput() {
  const {rows, turnCount} = playerOutputStats(GAME);
  const includesSetup = GAME.steps.some(step => step.turn === 0);
  el("outputNote").textContent = 'Entire recorded game · ' + turnCount + ' recorded turn' + (turnCount === 1 ? '' : 's') +
    (includesSetup ? ', including setup as one turn' : '') + '. Per-turn averages use this same period for every player, including silent turns and turns after elimination. ' +
    'Messages include public and private deliveries. Decision summaries count self-reported entries stored in the thought layer, not hidden provider reasoning. Lengths count whitespace-separated words; notes and tool calls are excluded.';
  el("outputCharts").innerHTML = [
    outputBarChart(rows, "messagesPerTurn", "Average messages per turn", "msg / turn", 2),
    outputBarChart(rows, "wordsPerMessage", "Average message length", "words / msg", 1),
    outputBarChart(rows, "thoughts", "Decision summaries", "entries"),
    outputBarChart(rows, "wordsPerThought", "Average summary length", "words / entry", 1),
  ].join("");
  el("outputTable").innerHTML = '<thead><tr><th scope="col">Player</th><th scope="col">Messages</th>' +
    '<th scope="col">Public</th><th scope="col">Private</th><th scope="col">Message words</th>' +
    '<th scope="col">Decision summaries</th><th scope="col">Summaries / turn</th><th scope="col">Summary words</th>' +
    '</tr></thead><tbody>' + rows.map(row => '<tr><th scope="row" style="color:' + colorOf(row.name) + '">' +
      escapeHtml(row.name) + '</th>' + [row.messages, row.publicMessages, row.privateMessages, row.messageWords,
        row.thoughts, row.thoughtsPerTurn, row.thoughtWords].map(value => '<td>' + outputNumber(value, 2) + '</td>').join("") +
      '</tr>').join("") + '</tbody>';
}

// The API calls sheet: summaries come from the server's trace endpoint; the full
// prompt/response of a call is fetched lazily when it is expanded.
let traceCalls = null;

function showStatsSheet(name) {
  el("overviewSheet").hidden = name !== "overview";
  el("evalSheet").hidden = name !== "evaluation";
  el("callsSheet").hidden = name !== "calls";
  el("tabOverview").classList.toggle("on", name === "overview");
  el("tabEval").classList.toggle("on", name === "evaluation");
  el("tabCalls").classList.toggle("on", name === "calls");
  if (name === "calls") loadCalls();
  if (name === "evaluation") renderEvalStats();
}

// the calls sheet reads whichever trace the source picker names: the game's own calls, or the
// viewed evaluation run's panel calls (versioned with the selector like everything else)
function callsQuery() {
  const source = el("callSource").value;
  const params = new URLSearchParams({ source });
  if (source === "evaluation" && EVAL.version) params.set("version", EVAL.version);
  return params.toString();
}

async function loadCalls() {
  const container = el("callsList");
  if (!TRACE_URL) { el("callsStatus").textContent = "no trace in a static export"; container.innerHTML = ""; return; }
  el("callsStatus").textContent = "loading…";
  const response = await fetch(`${TRACE_URL}?${callsQuery()}`, { cache: "no-store" });
  if (!response.ok) {
    el("callsStatus").textContent = response.status === 404 ? "no trace for this source" : "could not load the trace";
    container.innerHTML = "";
    return;
  }
  traceCalls = (await response.json()).calls;
  el("callsStatus").textContent = traceCalls.length
    ? `${traceCalls.length} calls` : "no API calls recorded for this game";
  const callers = [...new Set(traceCalls.map(call => call.caller))];
  const selected = el("callerFilter").value;
  el("callerFilter").innerHTML = '<option value="">all</option>' +
    callers.map(caller => `<option${caller === selected ? " selected" : ""}>${caller}</option>`).join("");
  renderCalls();
}

function renderCalls() {
  const filter = el("callerFilter").value;
  const rows = (traceCalls ?? []).filter(call => !filter || call.caller === filter).map(call => {
    const outcome = call.error ? `<span class="callerr">✗ ${escapeHtml(call.error)}</span>`
      : call.tool_call_names.length ? `→ ${call.tool_call_names.join(", ")}` : "→ text only";
    const tokens = call.input_tokens == null ? ""
      : `${call.input_tokens.toLocaleString("en-US")} → ${call.output_tokens.toLocaleString("en-US")} tok · `;
    return `<div class="call${call.error ? " failed" : ""}">
      <div class="callrow" onclick="toggleCallDetail(${call.seq})">
        <span class="meta">#${call.seq} · turn ${call.turn} · ${call.kind} · </span>${colorizeNames(call.caller)}
        <span class="meta"> · ${call.message_count} messages · ${tokens}${call.latency_ms} ms</span> ${outcome}
      </div>
      <div class="calldetail" id="detail-${call.seq}" hidden></div>
    </div>`;
  });
  el("callsList").innerHTML = rows.join("") || '<div class="empty">No calls match the filter.</div>';
}

async function toggleCallDetail(seq) {
  const detail = el("detail-" + seq);
  if (!detail.hidden) { detail.hidden = true; return; }
  if (!detail.innerHTML) {
    detail.innerHTML = '<div class="empty">loading…</div>';
    const record = await (await fetch(`${TRACE_URL}/${seq}?${callsQuery()}`)).json();
    detail.innerHTML = renderCallDetail(record);
  }
  detail.hidden = false;
}

function renderCallDetail(record) {
  const toolCallLines = calls => (calls ?? [])
    .map(call => `<div class="mtool">⚙ ${call.name}(${escapeHtml(JSON.stringify(call.arguments))})</div>`).join("");
  const messages = record.messages.map(message =>
    `<div class="m ${message.role}">
      <span class="role">${message.role}${message.tool_call_id ? ` · result for ${message.tool_call_id}` : ""}</span>
      ${message.content ? `<pre>${escapeHtml(message.content)}</pre>` : ""}${toolCallLines(message.tool_calls)}
    </div>`).join("");
  const response = record.response
    ? `<div class="m"><span class="role">⬅ response</span>
        ${record.response.text ? `<pre>${escapeHtml(record.response.text)}</pre>` : ""}
        ${toolCallLines(record.response.tool_calls)}</div>`
    : `<div class="m"><span class="role">⬅ error</span><pre>${escapeHtml(record.error ?? "")}</pre></div>`;
  const tools = record.tools.length
    ? `<details><summary>${record.tools.length} tools offered (schemas)</summary>
        <pre>${escapeHtml(JSON.stringify(record.tools, null, 2))}</pre></details>` : "";
  return messages + response + tools;
}
el("callerFilter").onchange = renderCalls;
el("callSource").onchange = loadCalls;
