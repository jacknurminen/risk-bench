/* ================= STATS MODAL (ported from the classic cockpit, restyled) ================= */
/* Hand-rolled SVG charts over the per-turn series computed in Python from the replay,
   the commentator-panel cumulative-valence charts, the per-turn token/cost stacks, the
   per-player table, and the API-call inspector. */
const MARKER_GLYPHS = { elimination: "✕", continent: "◆", trade: "▲" };
const VALENCE_CHARTS = [
  ["strong", "skill: strong play, cumulative"],
  ["weak", "skill: weak play, cumulative"],
  ["cooperative", "conduct: cooperative, cumulative"],
  ["adversarial", "conduct: adversarial, cumulative"],
  ["deceptive", "conduct: deception, cumulative"],
];
const memberColor = commentatorIndex => MEMBER_COLORS[commentatorIndex % MEMBER_COLORS.length];
const panelSize = () => GAME.commentators.length;
// the members currently included in the chart consensus; all of them until the viewer toggles some off
let visibleMembers = new Set(GAME.commentators.map(member => member.index));
// which per-instance agreement levels (k of the full panel) the charts count; all of them by default
let selectedAgreement = new Set(GAME.commentators.map((_, level) => level + 1));

function openStats() {
  renderStats();
  if (!el("callsSheet").hidden) loadCalls();
  el("statsModal").classList.add("open");
}
function closeStats() { el("statsModal").classList.remove("open"); hideChartTip(); }
const statsOpen = () => el("statsModal").classList.contains("open");

function renderStats() {
  el("statsLegend").innerHTML = GAME.players
    .map((player, seat) => `<span class="key"><span class="swatch" style="background:${COLORS[seat]}"></span>${tagOf(player.name)}</span>`).join("")
    + '<span class="markerKey">✕ elimination &nbsp;◆ continent control &nbsp;▲ card trade</span>';
  const seriesByPlayer = GAME.stats.series.players;
  const charts = el("charts");
  charts.innerHTML = "";
  if (GAME.stats.series.turns.length) {
    charts.appendChild(buildChart("army share", seriesByPlayer.map(s => s.army_share), v => Math.round(v * 100) + "%"));
    charts.appendChild(buildChart("territories", seriesByPlayer.map(s => s.territories), v => Math.round(v)));
    charts.appendChild(buildChart("income per turn", seriesByPlayer.map(s => s.income), v => Math.round(v)));
    charts.appendChild(buildChart("cards in hand", seriesByPlayer.map(s => s.cards), v => Math.round(v)));
  }
  renderMemberToggles();
  renderAgreementFilter();
  renderValenceCharts();
  renderTokenChart();
  renderStatsTable();
}

// The cumulative-valence charts are per-member: each player's line is the mean across the chosen
// members and the band is their spread, so no annotator's trajectory is hidden. The checkboxes pick
// which members feed the consensus (all by default); they only appear for a real panel.
function renderMemberToggles() {
  const container = el("memberToggles");
  if (panelSize() < 2) { container.innerHTML = ""; return; }
  container.innerHTML = '<span>commentator panel — line is the mean, band is the spread:</span>' +
    GAME.commentators.map(member =>
      `<label><input type="checkbox" data-member="${member.index}" ${visibleMembers.has(member.index) ? "checked" : ""}>` +
      `<span class="swatch" style="background:${memberColor(member.index)}"></span>${escapeHtml(member.label)}</label>`
    ).join("");
  container.querySelectorAll("input[data-member]").forEach(box => box.onchange = () => {
    const member = Number(box.dataset.member);
    if (box.checked) visibleMembers.add(member); else visibleMembers.delete(member);
    renderValenceCharts();
  });
}

// The agreement filter counts a tag only when the chosen number of members flagged that exact
// (turn, player, behavior) — measured over the FULL panel, independent of the member checkboxes.
function renderAgreementFilter() {
  const container = el("agreementFilter");
  const members = panelSize();
  if (members < 2) { container.innerHTML = ""; return; }
  const chips = Array.from({ length: members }, (_, i) => i + 1).map(level =>
    `<button data-level="${level}" class="${selectedAgreement.has(level) ? "on" : ""}">${level}/${members}</button>`
  ).join("");
  container.innerHTML = '<span>agreement filter — count a tag only when this many members flagged it:</span>' + chips;
  container.querySelectorAll("button[data-level]").forEach(button => button.onclick = () => {
    const level = Number(button.dataset.level);
    if (selectedAgreement.has(level)) selectedAgreement.delete(level); else selectedAgreement.add(level);
    button.classList.toggle("on");
    renderValenceCharts();
    renderStatsTable();  // the agreement filter drives the behavior-tags column too
  });
}

function renderValenceCharts() {
  const container = el("valenceCharts");
  if (!GAME.commentary.length) { container.innerHTML = ""; return; }
  const members = GAME.commentators.map(member => member.index).filter(member => visibleMembers.has(member));
  const agreement = tagInstanceAgreement();
  container.innerHTML = "";
  for (const [valence, title] of VALENCE_CHARTS) {
    container.appendChild(buildBandChart(title, members, filteredCumulatives(members, valence, agreement)));
  }
}

// How many distinct members flagged each (turn, player, behavior) — the per-instance agreement,
// over the whole panel. Built from the raw per-member tags the journal kept separately.
function tagInstanceAgreement() {
  const agreement = new Map();
  GAME.commentary.forEach(comment => comment.tags.forEach(tag => {
    const key = `${comment.turn}|${tag.player}|${tag.behavior}`;
    (agreement.get(key) ?? agreement.set(key, new Set()).get(key)).add(comment.commentator_index);
  }));
  return agreement;
}

// Per seat, per visible member, the cumulative count of that member's tags of this valence — counting
// a tag only when its panel agreement is among the selected levels.
function filteredCumulatives(members, valence, agreement) {
  const chartTurns = GAME.stats.series.turns;
  const turnPosition = new Map(chartTurns.map((turn, position) => [turn, position]));
  const chartValenceOf = GAME.stats.tag_valence ?? {};
  const counts = GAME.players.map(() => members.map(() => chartTurns.map(() => 0)));
  GAME.commentary.forEach(comment => {
    const memberPosition = members.indexOf(comment.commentator_index);
    const position = turnPosition.get(comment.turn);
    if (memberPosition < 0 || position === undefined) return;  // a hidden member, or a turn off the axis
    comment.tags.forEach(tag => {
      if (chartValenceOf[tag.behavior] !== valence) return;
      const seat = GAME.players.findIndex(player => player.name === tag.player);
      if (seat < 0) return;  // the commentator occasionally invents player names; drop those
      const agreed = agreement.get(`${comment.turn}|${tag.player}|${tag.behavior}`)?.size ?? 0;
      if (selectedAgreement.has(agreed)) counts[seat][memberPosition][position] += 1;
    });
  });
  return counts.map(perSeat => perSeat.map(perMember => {
    let running = 0;
    return perMember.map(increment => (running += increment));
  }));
}

// Shared scaffolding: hairline gridlines one step off the surface, mono axis labels, event markers.
function chartFrame(yMax, formatValue) {
  const W = 470, H = 230, PAD = { left: 38, right: 12, top: 26, bottom: 18 };
  const chartTurns = GAME.stats.series.turns;
  const xAt = i => PAD.left + (chartTurns.length < 2 ? 0 : i * (W - PAD.left - PAD.right) / (chartTurns.length - 1));
  const yAt = v => H - PAD.bottom - (v / yMax) * (H - PAD.top - PAD.bottom);
  const parts = [];
  for (const fraction of [0, 0.5, 1]) {
    const gy = yAt(yMax * fraction);
    parts.push(`<line x1="${PAD.left}" y1="${gy}" x2="${W - PAD.right}" y2="${gy}" stroke="#efece2"/>`);
    parts.push(`<text x="${PAD.left - 4}" y="${gy + 3}" class="axis" text-anchor="end">${formatValue(yMax * fraction)}</text>`);
  }
  parts.push(`<text x="${PAD.left}" y="${H - 4}" class="axis">turn ${chartTurns[0]}</text>`);
  parts.push(`<text x="${W - PAD.right}" y="${H - 4}" class="axis" text-anchor="end">turn ${chartTurns.at(-1)}</text>`);
  const markersByTurn = {};
  for (const marker of GAME.stats.markers) (markersByTurn[marker.turn] ??= []).push(marker);
  for (const [turn, markers] of Object.entries(markersByTurn)) {
    const turnIndex = chartTurns.indexOf(Number(turn));
    if (turnIndex < 0) continue;
    const mx = xAt(turnIndex);
    parts.push(`<line x1="${mx}" y1="${PAD.top - 6}" x2="${mx}" y2="${H - PAD.bottom}" stroke="#e2ddd0" stroke-dasharray="3 3"/>`);
    markers.forEach((marker, level) => {
      const seat = GAME.players.findIndex(player => player.name === marker.player);
      parts.push(`<text x="${mx}" y="${10 + level * 10}" class="marker" text-anchor="middle"
        fill="${COLORS[seat] ?? "#888"}">${MARKER_GLYPHS[marker.kind] ?? "•"}
        <title>turn ${turn}: ${escapeHtml(marker.text)}</title></text>`);
    });
  }
  return { W, H, PAD, turns: chartTurns, xAt, yAt, parts };
}

function chartElement(title, frame, hoverRows) {
  const chart = document.createElement("div");
  chart.className = "chart";
  chart.innerHTML = `<h4>${title}</h4><svg viewBox="0 0 ${frame.W} ${frame.H}">${frame.parts.join("")}</svg>`;
  if (hoverRows) wireChartHover(chart.querySelector("svg"), frame, hoverRows);
  return chart;
}

/* crosshair + tooltip: nearest turn on the x axis, one swatch+value row per player (text in ink) */
function wireChartHover(svg, frame, hoverRows) {
  const cross = document.createElementNS(SVG_NS, "line");
  cross.setAttribute("y1", frame.PAD.top); cross.setAttribute("y2", frame.H - frame.PAD.bottom);
  cross.setAttribute("stroke", "var(--gold)"); cross.setAttribute("opacity", "0");
  svg.appendChild(cross);
  svg.addEventListener("pointermove", event => {
    const rect = svg.getBoundingClientRect();
    const x = (event.clientX - rect.left) * frame.W / rect.width;
    let nearest = 0, nearestDistance = Infinity;
    frame.turns.forEach((_, i) => {
      const distance = Math.abs(frame.xAt(i) - x);
      if (distance < nearestDistance) { nearestDistance = distance; nearest = i; }
    });
    cross.setAttribute("x1", frame.xAt(nearest)); cross.setAttribute("x2", frame.xAt(nearest));
    cross.setAttribute("opacity", "0.7");
    const tip = el("chartTip");
    tip.innerHTML = `<div class="tipTurn">turn ${frame.turns[nearest]}</div>` + hoverRows(nearest).map(row =>
      `<div class="tipRow"><span class="swatch" style="background:${row.color}"></span>${row.label} <b style="margin-left:auto;padding-left:10px">${row.value}</b></div>`).join("");
    tip.style.display = "block";
    tip.style.left = Math.min(event.clientX + 14, window.innerWidth - tip.offsetWidth - 10) + "px";
    tip.style.top = (event.clientY + 14) + "px";
  });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("opacity", "0"); hideChartTip(); });
}
function hideChartTip() { el("chartTip").style.display = "none"; }

function buildChart(title, valuesBySeat, formatValue) {
  // integer-valued charts round the axis up to an even maximum so the midpoint label is exact
  const dataMax = Math.max(...valuesBySeat.flat()) || 1;
  const yMax = valuesBySeat.flat().every(Number.isInteger) ? Math.max(2, Math.ceil(dataMax / 2) * 2) : dataMax;
  const frame = chartFrame(yMax, formatValue);
  valuesBySeat.forEach((values, seat) => {
    const points = values.map((value, i) => `${frame.xAt(i)},${frame.yAt(value)}`).join(" ");
    frame.parts.push(`<polyline points="${points}" fill="none" stroke="${COLORS[seat]}" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>`);
    // one end-dot per series with a surface ring, not a dot on every point
    const last = values.length - 1;
    frame.parts.push(`<circle cx="${frame.xAt(last)}" cy="${frame.yAt(values[last])}" r="4" fill="${COLORS[seat]}"
      stroke="var(--subtle-warm)" stroke-width="2"/>`);
  });
  return chartElement(title, frame, i => valuesBySeat.map((values, seat) =>
    ({ color: COLORS[seat], label: tagOf(GAME.players[seat].name), value: formatValue(values[i]) })));
}

// One cumulative-valence chart per metric: each player's line is the mean across the visible panel
// members, with a shaded band spanning their min–max so annotator disagreement shows.
function buildBandChart(title, members, cumulatives) {
  const chartTurns = GAME.stats.series.turns;
  const bands = cumulatives.map(perMember =>
    chartTurns.map((_, t) => {
      const values = perMember.map(memberValues => memberValues[t] ?? 0);
      if (!values.length) return { mean: 0, lo: 0, hi: 0 };
      return {
        mean: values.reduce((sum, value) => sum + value, 0) / values.length,
        lo: Math.min(...values), hi: Math.max(...values),
      };
    })
  );
  const dataMax = Math.max(1, ...bands.flat().map(point => point.hi));
  const yMax = Math.max(2, Math.ceil(dataMax / 2) * 2);
  const frame = chartFrame(yMax, v => Math.round(v));
  const showBand = members.length > 1;
  bands.forEach((band, seat) => {
    if (showBand) {
      const upper = band.map((point, i) => `${frame.xAt(i)},${frame.yAt(point.hi)}`);
      const lower = band.map((point, i) => `${frame.xAt(i)},${frame.yAt(point.lo)}`).reverse();
      frame.parts.push(`<polygon points="${upper.concat(lower).join(" ")}" fill="${COLORS[seat]}" fill-opacity="0.1"/>`);
    }
    const points = band.map((point, i) => `${frame.xAt(i)},${frame.yAt(point.mean)}`).join(" ");
    frame.parts.push(`<polyline points="${points}" fill="none" stroke="${COLORS[seat]}" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>`);
    const last = band.length - 1;
    frame.parts.push(`<circle cx="${frame.xAt(last)}" cy="${frame.yAt(band[last].mean)}" r="4" fill="${COLORS[seat]}"
      stroke="var(--subtle-warm)" stroke-width="2"/>`);
  });
  return chartElement(title, frame, i => bands.map((band, seat) =>
    ({ color: COLORS[seat], label: tagOf(GAME.players[seat].name),
       value: showBand ? `${band[i].mean.toFixed(1)} (${band[i].lo}–${band[i].hi})` : String(Math.round(band[i].mean)) })));
}

// Call-type colors come from the theme accents rather than reusing player hues (the "by model" mode
// IS player-colored, so the two stack dimensions must not share a vocabulary); "other" reads as the
// conventional gray remainder. The set passes the categorical checks on the chart surface.
const TOKEN_COLORS = { negotiation: "#2e7cb0", decision: "#1f8a5b", commentary: "#c99a2e", other: "#8b8472" };
const fmtTokens = n =>
  n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(Math.round(n));
const callerColor = caller => {
  const seat = GAME.players.findIndex(player => player.name === caller);
  return seat >= 0 ? COLORS[seat] : "#8a8f98";  // commentators (and any stray caller) read neutral grey
};

function renderTokenChart() {
  const container = el("tokenChart");
  const usage = GAME.stats.per_turn_usage;
  container.innerHTML = usage?.turns.length ? "" : '<div class="empty">No traced API calls.</div>';
  if (usage?.turns.length) container.appendChild(buildTokenChart(usage));
}

// One stacked column per turn for input (left) and output (right). The toggles pick the
// measure (tokens or estimated cost) and the stack dimension (call type, or by model).
function buildTokenChart(usage) {
  const measure = el("tokMeasure").value, byModel = el("tokBreakdown").value === "model";
  const groups = byModel ? usage.callers : usage.categories;
  const series = byModel ? usage.by_caller : usage.by_category;
  const [leftKey, rightKey] = measure === "cost" ? ["input_cost", "output_cost"] : ["input_tokens", "output_tokens"];
  const colorFor = byModel ? callerColor : (group => TOKEN_COLORS[group] ?? "#888");
  const fmt = measure === "cost" ? (v => "$" + v.toFixed(2)) : fmtTokens;
  const value = (group, key, i) => series[group]?.[key]?.[i] ?? 0;
  const W = 960, H = 250, PAD = { left: 54, right: 12, top: 14, bottom: 22 };
  const chartTurns = usage.turns;
  const chart = document.createElement("div");
  chart.className = "chart";
  const legend = groups.map(group =>
    `<span class="key"><span class="swatch" style="background:${colorFor(group)};border-radius:2px"></span>${escapeHtml(group)}</span>`).join("");
  const stackTotal = (key, i) => groups.reduce((sum, group) => sum + value(group, key, i), 0);
  let dataMax = 0;
  chartTurns.forEach((_, i) => dataMax = Math.max(dataMax, stackTotal(leftKey, i), stackTotal(rightKey, i)));
  dataMax = dataMax || 1;
  const plotW = W - PAD.left - PAD.right, plotH = H - PAD.top - PAD.bottom;
  const yAt = v => H - PAD.bottom - (v / dataMax) * plotH;
  const slot = plotW / chartTurns.length, barW = Math.min(24, Math.max(0.8, slot * 0.4));
  const parts = [];
  for (const fraction of [0, 0.5, 1]) {
    const gy = yAt(dataMax * fraction);
    parts.push(`<line x1="${PAD.left}" y1="${gy}" x2="${W - PAD.right}" y2="${gy}" stroke="#efece2"/>`);
    parts.push(`<text x="${PAD.left - 4}" y="${gy + 3}" class="axis" text-anchor="end">${fmt(dataMax * fraction)}</text>`);
  }
  parts.push(`<text x="${PAD.left}" y="${H - 5}" class="axis">turn ${chartTurns[0]}</text>`);
  parts.push(`<text x="${W - PAD.right}" y="${H - 5}" class="axis" text-anchor="end">turn ${chartTurns.at(-1)}</text>`);
  const stack = (key, x, i) => {
    const segments = [];
    let base = 0;
    for (const group of groups) {
      const v = value(group, key, i);
      if (v > 0) segments.push({ group, v, base });
      base += v;
    }
    segments.forEach((segment, position) => {
      const top = yAt(segment.base + segment.v);
      // a 1px surface gap separates touching segments (shaved off every segment's top except the topmost)
      const gap = position < segments.length - 1 ? 1 : 0;
      const height = Math.max(0, yAt(segment.base) - top - gap);
      parts.push(`<rect x="${x.toFixed(2)}" y="${(top + gap).toFixed(2)}" width="${barW.toFixed(2)}" height="${height.toFixed(2)}"
        fill="${colorFor(segment.group)}"><title>turn ${chartTurns[i]} · ${key.startsWith("input") ? "input" : "output"} · ${segment.group}: ${fmt(segment.v)}</title></rect>`);
    });
  };
  chartTurns.forEach((_, i) => {
    const x0 = PAD.left + i * slot;
    stack(leftKey, x0 + slot * 0.08, i);
    stack(rightKey, x0 + slot * 0.52, i);
  });
  chart.innerHTML = `<div class="tokenLegend">${legend}</div><svg viewBox="0 0 ${W} ${H}">${parts.join("")}</svg>`;
  return chart;
}
["tokMeasure", "tokBreakdown"].forEach(id => el(id).addEventListener("change", renderTokenChart));

function usageCells(caller) {
  const usage = (GAME.stats.usage ?? {})[caller];
  if (!usage) return "<td>—</td><td>—</td><td>—</td>";
  const failed = usage.errors ? ` <span class="callerr" style="color:var(--adversarial)">(${usage.errors} failed)</span>` : "";
  const cost = usage.cost_usd == null ? "—" : `$${usage.cost_usd.toFixed(2)}`;
  return `<td class="mono" style="font-size:11px">${usage.calls}${failed}</td>
    <td class="mono" style="font-size:11px">${usage.input_tokens.toLocaleString("en-US")} / ${usage.output_tokens.toLocaleString("en-US")}</td>
    <td class="mono" style="font-size:11px">${cost}</td>`;
}

// Per seat, the behavior-tag counts for the table, honoring the same agreement filter as the charts:
// a tag occurrence counts only when its (turn, player, behavior) instance reached a selected agreement
// level over the FULL panel. Member toggles do not apply here — the table is a whole-panel aggregate.
function filteredTableTags() {
  const perInstance = GAME.players.map(() => ({}));  // seat -> "behavior turn" -> { members:Set, occ }
  GAME.commentary.forEach(comment => comment.tags.forEach(tag => {
    const seat = GAME.players.findIndex(player => player.name === tag.player);
    if (seat < 0) return;  // the commentator occasionally invents player names; drop those
    const key = tag.behavior + " " + comment.turn;
    const instance = perInstance[seat][key] ?? (perInstance[seat][key] = { members: new Set(), occ: 0 });
    instance.members.add(comment.commentator_index);
    instance.occ += 1;
  }));
  return GAME.players.map((_, seat) => {
    const tags = {};
    for (const [key, instance] of Object.entries(perInstance[seat])) {
      if (!selectedAgreement.has(instance.members.size)) continue;
      const behavior = key.split(" ")[0];
      const entry = tags[behavior] ?? (tags[behavior] = { count: 0, byLevel: {} });
      entry.count += instance.occ;
      entry.byLevel[instance.members.size] = (entry.byLevel[instance.members.size] ?? 0) + 1;
    }
    return tags;
  });
}

function tagChips(tags) {
  const members = panelSize();
  return Object.entries(tags).sort((a, b) => b[1].count - a[1].count).map(([behavior, info]) => {
    const breakdown = members > 1
      ? " · agreement " + Object.keys(info.byLevel).map(Number).sort((a, b) => b - a)
          .map(level => `${level}/${members}×${info.byLevel[level]}`).join(", ")
      : "";
    const title = `${info.count} tag${info.count === 1 ? "" : "s"}${breakdown}`;
    const [vc, vt] = valenceStyle(behavior);
    return `<span class="tagchip" style="color:${vc};background:${vt}" title="${escapeHtml(title)}">` +
      `${behavior}${info.count > 1 ? ` ×${info.count}` : ""}</span>`;
  }).join("");
}

function tagFilterNote() {
  const members = panelSize();
  if (members < 2) return "";
  const levels = [...selectedAgreement].sort((a, b) => a - b);
  if (!levels.length) return "Behaviour tags: no agreement level is selected above, so nothing counts.";
  if (levels.length === members) return "Behaviour tags count every annotation — use the agreement chips above to filter by panel consensus.";
  return `Behaviour tags count only annotations at ${levels.map(level => `${level}/${members}`).join(", ")} panel agreement (set above).`;
}

function renderStatsTable() {
  const tagsBySeat = filteredTableTags();
  el("tagFilterNote").textContent = tagFilterNote();
  const rows = GAME.players.map((player, seat) => {
    const playerStats = GAME.stats.per_player[seat];
    return `<tr><td style="color:${COLORS[seat]};font-weight:600" class="mono">${tagOf(player.name)}</td>
      <td class="mono" style="font-size:11px">${playerStats.attacks}</td><td class="mono" style="font-size:11px">${playerStats.conquests}</td>
      <td class="mono" style="font-size:11px">${playerStats.armies_destroyed} / ${playerStats.armies_lost}</td>
      <td class="mono" style="font-size:11px">${playerStats.public_messages} 📢 &nbsp;${playerStats.private_messages} 🔒</td>
      ${usageCells(player.name)}
      <td>${tagChips(tagsBySeat[seat]) || "—"}</td></tr>`;
  });
  GAME.commentators.forEach(member => {
    rows.push(`<tr><td style="color:${memberColor(member.index)};font-weight:600" class="mono">🎙️ ${escapeHtml(member.label)}</td>
      <td>—</td><td>—</td><td>—</td><td>—</td>${usageCells(`commentator[${member.index}]`)}<td>—</td></tr>`);
  });
  el("statsTable").innerHTML = `<thead><tr><th>player</th><th>attacks</th><th>conquests</th>
    <th>armies destroyed / lost</th><th>messages</th>
    <th>api calls</th><th>tokens in / out</th><th>est. cost</th>
    <th>behavior tags</th></tr></thead>
    <tbody>${rows.join("")}</tbody>`;
}

// The API calls sheet: summaries come from the server's trace endpoint; the full
// prompt/response of a call is fetched lazily when it is expanded.
let traceCalls = null;

function showStatsSheet(name) {
  el("overviewSheet").hidden = name !== "overview";
  el("callsSheet").hidden = name !== "calls";
  el("tabOverview").classList.toggle("on", name === "overview");
  el("tabCalls").classList.toggle("on", name === "calls");
  if (name === "calls") loadCalls();
}

async function loadCalls() {
  const container = el("callsList");
  el("callsStatus").textContent = "loading…";
  const response = await fetch(TRACE_URL, { cache: "no-store" });
  if (!response.ok) { el("callsStatus").textContent = "could not load the trace"; return; }
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
    const record = await (await fetch(`${TRACE_URL}/${seq}`)).json();
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

