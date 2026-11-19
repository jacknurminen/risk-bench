/* Annotation review inside the game view.
   Active only when the page is opened with ?review=<tournament>/<sample>&item=<item id>.
   Findings open in the existing annotation panel; messages are coded blind, with research
   annotations switched off for the whole message queue. */
const reviewQuery = new URLSearchParams(location.search);
const reviewRef = reviewQuery.get("review");
let reviewSample = null, reviewItem = null;

const reviewTournament = () => reviewRef.split("/")[0];
const reviewSampleId = () => reviewRef.split("/")[1];
const reviewApi = () => `/api/tournaments/${encodeURIComponent(reviewTournament())}/reviews/${encodeURIComponent(reviewSampleId())}`;
const reviewHubUrl = () => `/tournaments/${encodeURIComponent(reviewTournament())}/review?sample=${encodeURIComponent(reviewSampleId())}`;
const currentGameId = () => decodeURIComponent(location.pathname.split("/").pop());

function setReviewBlind(blind) {
  reviewBlind = blind;
  researchVisibilityChosen = true;
  state.filters.showCommentary = !blind;
  if (blind) { state.mode = "read"; closePanels(); }
  el("commToggle").classList.toggle("locked", blind);
  renderModeToggle(); renderFilters(); renderEvalBar(); renderSynthesis(); renderTimeline();
}

// Expand the turn, wind the board to the line, and bring the highlighted line into view.
function jumpToReviewSpan(span) {
  const line = spanById.get(span);
  if (!line) { toast(`Span ${span} is not in this game's trace.`); return; }
  reviewTargetSpan = span;
  state.mode = "read";
  state.expanded.add(line.turn);
  renderModeToggle(); renderTimeline();
  stopPlaying(); show(line.step);
  requestAnimationFrame(() => el("trace-span-" + span)?.scrollIntoView({ block: "center" }));
}

function showReviewFinding() {
  const item = reviewItem;
  if (item.unit_type === "deception_candidate") { jumpToReviewSpan(item.span); return; }
  jumpToReviewSpan(item.span);
  openUnitDrawer(`${reviewSample.evaluator_index}:${item.unit_type}:${item.unit_id}`);
}

function reviewItemSummary(item) {
  if (item.kind === "message") {
    const line = spanById.get(item.span);
    return `<p class="reviewWhat">Message from <b>${escapeHtml(line?.player || "")}</b>, ${escapeHtml(gameTimeLabel(line?.turn, line?.round))}.
      It is highlighted in the timeline. Research annotations stay hidden for the whole message queue.</p>
      <button class="btn" type="button" onclick="jumpToReviewSpan(${item.span})">Jump to message</button>`;
  }
  const record = item.record;
  const title = record.terms || record.proposition || record.id;
  let html = `<p class="reviewWhat"><span class="kicker">${escapeHtml(item.unit_type.replace("_", " "))} · ${escapeHtml(item.stratum)}</span><br>
    <b>${escapeHtml(record.actor)}</b>: ${escapeHtml(title)}</p>`;
  if (item.unit_type === "deception_candidate") {
    // Candidates are not in the annotation panel, so their record is shown here.
    html += `<details class="reviewFinding" open><summary>Candidate record</summary>${findingHtml(item)}</details>`;
  } else {
    html += `<button class="btn" type="button" onclick="showReviewFinding()">Open annotation</button>`;
  }
  return html;
}

function renderReviewPanel(message = "") {
  const side = el("reviewSide");
  const item = reviewItem;
  const queue = queueItems(reviewSample, item.kind);
  const position = queue.indexOf(item);
  const done = queue.filter(candidate => candidate.verdict).length;
  const title = item.kind === "finding" ? "Findings" : "Messages (blind)";
  side.innerHTML = `<div class="card reviewCard">
    <div class="reviewHead"><span class="kicker">Annotation review · ${title}</span>
      <a href="${escapeHtml(reviewHubUrl())}">sample</a> <a href="${escapeHtml(location.pathname)}" title="Leave review mode">×</a></div>
    <div class="reviewNav">
      <button class="btn" type="button" onclick="openReviewItem(${position > 0 ? `'${queue[position - 1].item_id}'` : "null"})" ${position > 0 ? "" : "disabled"}>←</button>
      <span class="mono">${position + 1} / ${queue.length} · ${escapeHtml(item.item_id)}${item.verdict ? ' · <span class="reviewed">reviewed</span>' : ""}</span>
      <button class="btn" type="button" onclick="openReviewItem(${position < queue.length - 1 ? `'${queue[position + 1].item_id}'` : "null"})" ${position < queue.length - 1 ? "" : "disabled"}>→</button>
      <button class="btn" type="button" onclick="openNextOpenReviewItem()">Next unreviewed</button>
    </div>
    <div class="reviewProgress mono">${done} of ${queue.length} reviewed</div>
    ${reviewItemSummary(item)}
    <form id="reviewForm" class="verdict">${questionsHtml(item)}
      <label class="noteField">Note <textarea id="reviewNote" rows="2" placeholder="${item.kind === "finding"
        ? "What is wrong or uncertain, briefly" : "Optional: what decided it, or why unsure"}">${escapeHtml(item.verdict?.note || "")}</textarea></label>
      <div class="reviewActions"><button class="btn dark" type="submit">Save and next (Ctrl+Enter)</button>
        <button class="btn" type="button" onclick="clearReviewVerdict()">Clear</button></div>
      <p class="reviewMessage" role="status">${escapeHtml(message)}</p>
    </form></div>`;
  el("reviewForm").addEventListener("submit", saveReviewVerdict);
}

async function openReviewItem(itemId) {
  if (!itemId) return;
  const item = reviewSample.items.find(candidate => candidate.item_id === itemId);
  if (!item) { toast("That item is not in this sample."); return; }
  const url = reviewUrl(reviewTournament(), reviewSample, item);
  const run = reviewSample.runs.find(candidate => candidate.game_id === item.game_id);
  // Another game, or another evaluation run of this one, needs its own page.
  if (item.game_id !== currentGameId() || (reviewQuery.get("version") || "") !== run.run_id) {
    location.href = url;
    return;
  }
  history.replaceState(null, "", url);
  reviewItem = item;
  if (item.kind === "message") {
    setReviewBlind(true);
    jumpToReviewSpan(item.span);
  } else {
    setReviewBlind(false);
    showReviewFinding();
  }
  renderReviewPanel();
}

function openNextOpenReviewItem() {
  const queue = queueItems(reviewSample, reviewItem.kind);
  const next = nextOpenIndex(queue, queue.indexOf(reviewItem));
  if (next < 0) { renderReviewPanel(`All ${queue.length} items in this queue are reviewed.`); return; }
  openReviewItem(queue[next].item_id);
}

async function putReviewVerdict(body) {
  const response = await fetch(`${reviewApi()}/items/${encodeURIComponent(reviewItem.item_id)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || `Could not save (${response.status})`);
  return result;
}

async function saveReviewVerdict(event) {
  event.preventDefault();
  const form = el("reviewForm");
  const verdict = collectVerdict(reviewItem, field => form.querySelector(`input[name="${field}"]:checked`)?.value);
  if (!verdict) { renderReviewPanelMessage("Answer every question; use unsure where the evidence does not decide it."); return; }
  try {
    reviewItem.verdict = await putReviewVerdict({ verdict, note: el("reviewNote").value });
  } catch (error) {
    renderReviewPanelMessage(error.message);
    return;
  }
  toast("Review saved");
  openNextOpenReviewItem();
}

async function clearReviewVerdict() {
  try {
    await putReviewVerdict({ verdict: null });
  } catch (error) {
    renderReviewPanelMessage(error.message);
    return;
  }
  reviewItem.verdict = null;
  renderReviewPanel("Verdict cleared.");
}

function renderReviewPanelMessage(message) {
  const status = document.querySelector("#reviewSide .reviewMessage");
  if (status) status.textContent = message;
}

async function startReviewPanel() {
  const itemId = reviewQuery.get("item");
  const side = el("reviewSide");
  document.body.classList.add("reviewing");
  side.hidden = false;
  // Hide annotations before the sample loads, so a blind item never shows them even briefly.
  if (itemId?.startsWith("m")) setReviewBlind(true);
  side.innerHTML = '<div class="card reviewCard"><p class="reviewWhat">Loading review…</p></div>';
  try {
    const response = await fetch(reviewApi());
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Could not load the review sample.");
    reviewSample = await response.json();
  } catch (error) {
    side.innerHTML = `<div class="card reviewCard"><p class="reviewWhat">${escapeHtml(error.message)}</p></div>`;
    return;
  }
  const item = reviewSample.items.find(candidate => candidate.item_id === itemId) || queueItems(reviewSample, "finding")[0];
  if (item) openReviewItem(item.item_id);
}

document.addEventListener("keydown", event => {
  if (reviewItem && event.key === "Enter" && (event.ctrlKey || event.metaKey)) el("reviewForm")?.requestSubmit();
});

if (reviewRef) startReviewPanel();
