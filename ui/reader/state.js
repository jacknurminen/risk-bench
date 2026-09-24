/* The server injects the page data as window.BOOT (see reader.html.j2); unpack it under
   the names the rest of the reader has always used. All reader scripts are classic scripts
   loaded in order, sharing this one global scope. */
const GAME = window.BOOT.game;
let EVAL = window.BOOT.evaluation;
const LIVE = window.BOOT.live;
const POLL_URL = window.BOOT.urls.poll;
const EVAL_POLL_URL = window.BOOT.urls.evaluation_poll;
const EVALUATE_URL = window.BOOT.urls.evaluate;
const VERIFICATIONS_URL = window.BOOT.urls.verifications;
const TRACE_URL = window.BOOT.urls.trace;
const ANALYSIS_URL = window.BOOT.urls.analysis;
let gameRunning = window.BOOT.live_running;  // whether the game's worker is currently playing (live games only)
// which evaluation run is being viewed: "" follows the current (newest) run — so a re-run or a live
// game's fresh evaluation shows automatically — while a specific run id pins an older archived run.
let selectedVersion = "";
const viewedVersionRunId = () => selectedVersion || null;


// seat identity colors — hues chosen (and chroma-tuned) to pass the categorical checks on a white
// chart surface: lightness band, chroma floor, CVD adjacent-pair separation, and 3:1 contrast
const COLORS = ["#a33f3b","#3670ab","#6e8f3a","#71509c","#c97a29","#1f92ab"];
const MEMBER_COLORS = ["#7a6a55","#4a7a6e","#8a6f9e","#9e6a4a","#5a7a3c","#3c6a8a","#9a8a3c","#6a6a6a"];
GAME.notes ??= []; GAME.commentary ??= []; GAME.commentators ??= [];
const steps = GAME.steps;
const humanPlayers = new Set(GAME.human_players || []);
let researchVisibilityChosen = false;
let hideOpponentMissions = false;
try { hideOpponentMissions = localStorage.getItem("risk-bench:hide-opponent-missions") === "true"; } catch {}
let index = 0, timer = null;

const el = id => document.getElementById(id);
const pretty = name => (name ?? "").replaceAll("_", " ");
const placeName = name => pretty(name).replace(/\b\w/g, letter => letter.toUpperCase());
const gameTimeLabel = (turn, round) => turn === 0 ? "Setup" : `${round ? `Round ${round} · ` : ""}Turn ${turn}`;
let humanPending = false;
function readerStatus() {
  if (GAME.result) return GAME.result.winner ? "Game over" : "Draw";
  if (!gameRunning) return "Paused";
  return humanPending ? "Waiting for you" : "Agents playing";
}
const escapeHtml = t => (t ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("\'", "&#39;");

/* ---- player identity ---- */
const seatOf = {}; GAME.players.forEach((p, seat) => seatOf[p.name] = seat);
const informalToken = name => name.split(/[-#.:\s]/)[0].toLowerCase();
const tagOf = name => informalToken(name);
const initialsOf = name => informalToken(name).slice(0, 2).toUpperCase();
const colorOf = name => COLORS[seatOf[name] ?? 0];

/* name + place highlighting (ported from the classic viewer) */
const seatByAlias = {};
{
  const counts = {}; GAME.players.forEach(p => counts[informalToken(p.name)] = (counts[informalToken(p.name)] || 0) + 1);
  GAME.players.forEach((p, seat) => { seatByAlias[p.name.toLowerCase()] = seat;
    const inf = informalToken(p.name); if (inf && counts[inf] === 1) seatByAlias[inf] = seat; });
}
const NAME_PATTERN = new RegExp("\\b(" + Object.keys(seatByAlias).sort((a, b) => b.length - a.length)
  .map(a => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\b", "gi");
const colorizeNames = t => escapeHtml(t).replace(NAME_PATTERN, m =>
  `<span style="color:${COLORS[seatByAlias[m.toLowerCase()]]};font-weight:600">${m}</span>`);
const CONTINENT_TERRITORIES = {}; GAME.continents.forEach(c => CONTINENT_TERRITORIES[c.name] = c.territories);
const CONTINENT_OF = {}; GAME.continents.forEach(c => c.territories.forEach(t => CONTINENT_OF[t] = c.name));
const PLACE_PATTERN = new RegExp("\\b(" + [...Object.keys(steps[0].board), ...GAME.continents.map(c => c.name)]
  .sort((a, b) => b.length - a.length).join("|") + ")\\b", "g");
const richText = t => colorizeNames(t).replace(PLACE_PATTERN, m => `<span class="tlink" data-place="${m}">${m}</span>`);

/* Research units are identified within one evaluator run. */
const annotators = () => EVAL.annotators || [];
const annotatorColor = idx => MEMBER_COLORS[idx % MEMBER_COLORS.length];
const annotatorLabel = idx => annotators().find(a => a.index === idx)?.label || "evaluator";
const allUnits = () => (EVAL.members || []).flatMap(m =>
  ["agreements", "episodes", "claims"].flatMap(kind => (m[kind] || []).map(u =>
    ({...u, kind: kind.slice(0,-1), annotator: m.index}))));
const unitVisible = u => state.filters.annotators.has(u.annotator)
  && (!state.filters.kind || u.kind === state.filters.kind)
  && (!state.filters.validOnly || u.valid)
  && (!state.filters.actor || u.actor === state.filters.actor || u.initiator === state.filters.actor)
  && (!state.filters.code || (u.codes || []).includes(state.filters.code))
  && (!state.filters.status || u.status === state.filters.status)
  && (!state.filters.basis || (state.filters.basis === "unknown" ? u.basis == null : u.basis === state.filters.basis));
const unitKey = u => u.annotator + ":" + u.kind + ":" + u.id;
function unitCitations(value) {
  if (Array.isArray(value)) return value.flatMap(unitCitations);
  if (!value || typeof value !== "object") return [];
  if ("span" in value && "quote" in value) return [value];
  return Object.values(value).flatMap(unitCitations);
}
/* ---- trace spans grouped by turn (rebuilt on every evaluation refresh, so the trace grows live) ---- */
let spanById = new Map(), spansByTurn = new Map(), turns = [], actorByTurn = {}, roundByTurn = {}, lastNoteSpanByPlayer = {};
function rebuildTrace() {
  spanById = new Map((EVAL.spans || []).map(s => [s.span, s]));
  spansByTurn = new Map();
  (EVAL.spans || []).forEach(s => { if (!spansByTurn.has(s.turn)) spansByTurn.set(s.turn, []); spansByTurn.get(s.turn).push(s); });
  turns = [...spansByTurn.keys()].sort((a, b) => a - b);
  // the acting player of a turn is whoever the engine had take the board actions — not whoever
  // happened to speak first during negotiation (which was pinning every card to one player)
  actorByTurn = {};
  roundByTurn = {};
  steps.forEach(s => { if (s.current_player != null) actorByTurn[s.turn] = s.current_player; });
  steps.forEach(s => { if (s.round != null) roundByTurn[s.turn] = s.round; });
  // remember each player's most recent note span; only that one stays in the feed (spans are in order)
  lastNoteSpanByPlayer = {};
  (EVAL.spans || []).forEach(s => { if (s.layer === "note" && s.player != null) lastNoteSpanByPlayer[s.player] = s.span; });
}
rebuildTrace();
function turnActor(turn) {
  if (actorByTurn[turn] != null) return actorByTurn[turn];
  const ls = spansByTurn.get(turn) || [];
  return (ls.find(s => s.layer === "action") || ls.find(s => s.player) || {}).player || GAME.players[0].name;
}
// turn 0 is the initial army placement, where the engine cycles current_player through every seat to place
// starting armies — it belongs to no single player (phase "setup_placement"), so the timeline labels it by
// phase rather than picking the misleading owner actorByTurn's last-write-wins would otherwise land on.
const isSetupTurn = turn => turn === 0;
const turnStep = turn => { const ls = spansByTurn.get(turn) || []; return ls.length ? Math.max(...ls.map(s => s.step)) : 0; };
const turnOfStep = step => (steps[step] || {}).turn ?? turns[0];
/* Summarize resolved engine events, so repeated attack rolls are counted once each
   and an action followed by its event never double-counts placements. */
function turnHighlights(turn) {
  const lines = (spansByTurn.get(turn) || []).filter(s => s.layer === "event");
  const count = pattern => lines.filter(s => pattern.test(s.text)).length;
  const placed = lines.reduce((sum, s) => sum + Number(s.text.match(/ placed (\d+) armies on /)?.[1] || 0), 0);
  return [
    ["placement", placed, `${placed} ${placed === 1 ? "army" : "armies"} placed`],
    ["attack", count(/ attacked .* from .*:/), "battles"],
    ["conquest", count(/ conquered .* from /), "conquests"],
    ["fortify", count(/ fortified, moving /), "fortifications"],
    ["elimination", count(/ was ELIMINATED by /), "eliminations"],
  ].filter(([, n]) => n).map(([kind, count, label]) => ({kind, count,
    label: kind === "placement" ? label : `${count} ${count === 1 ? label.slice(0,-1) : label}`}));
}
function turnSummary(turn) {
  const lines = spansByTurn.get(turn) || [];
  const win = lines.find(s => s.layer === "event" && /WON|ELIMINATED/.test(s.text));
  if (win) return win.text;
  const conquest = lines.filter(s => s.layer === "event" && / conquered /.test(s.text)).at(-1);
  if (conquest) return conquest.text;
  const move = lines.filter(s => s.layer === "event" && / placed \d+ armies on | attacked .* from .*:| fortified, moving /.test(s.text)).at(-1);
  if (move) return move.text;
  const action = lines.find(s => s.layer === "action");
  const message = lines.find(s => s.layer === "message");
  return action?.text || message?.text.replace(/^[^:]*:\s*/, "") || "Waiting for moves";
}

/* ---- reader state ---- */
const state = {
  expanded: new Set(), openPrivate: new Set(), openAnno: new Set(), openEvidence: new Set(), openUnits: new Set(), mode: "read", focusTurn: turns[0] ?? 0,
  filters: {
    actor: "", code: "", status: "", basis: "", kind: "", validOnly: false,
    annotators: new Set(annotators().map(a => a.index)),
    showPrivate: true, showCommentary: !(humanPlayers.size && !GAME.result),
  },
  drawer: null,
};
// annotators the filter set has already decided on; lets a panel that finishes after page
// load default to visible without re-enabling members the viewer explicitly hid
const knownAnnotators = new Set(state.filters.annotators);
