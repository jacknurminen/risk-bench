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
let gameRunning = window.BOOT.live_running;  // whether the game's worker is currently playing (live games only)


// seat identity colors — hues chosen (and chroma-tuned) to pass the categorical checks on a white
// chart surface: lightness band, chroma floor, CVD adjacent-pair separation, and 3:1 contrast
const COLORS = ["#a33f3b","#3670ab","#6e8f3a","#71509c","#c97a29","#1f92ab"];
const MEMBER_COLORS = ["#7a6a55","#4a7a6e","#8a6f9e","#9e6a4a","#5a7a3c","#3c6a8a","#9a8a3c","#6a6a6a"];
GAME.notes ??= []; GAME.commentary ??= []; GAME.commentators ??= [];
const steps = GAME.steps;
let index = 0, timer = null;

const el = id => document.getElementById(id);
const pretty = name => (name ?? "").replaceAll("_", " ");
const escapeHtml = t => (t ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

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

/* ---- behaviour vocabulary + valence ---- */
const VOCAB = {}; (EVAL.vocabulary || []).forEach(v => VOCAB[v.behavior] = v);
const axisOf = b => VOCAB[b]?.axis ?? "conduct";
const valenceOf = b => EVAL.tag_valence?.[b] ?? null;
const AXES = [["skill", "Game skill", "var(--axis-skill)"], ["conduct", "Social conduct", "var(--axis-conduct)"], ["affect", "Affect", "var(--axis-affect)"]];
const AXIS_COLOR = { skill: "var(--axis-skill)", conduct: "var(--axis-conduct)", affect: "var(--axis-affect)" };
function valenceStyle(behavior) {
  const v = valenceOf(behavior);
  const map = { cooperative: ["var(--coop)", "var(--coop-tint)"], strong: ["var(--coop)", "var(--coop-tint)"],
    adversarial: ["var(--adversarial)", "var(--adversarial-tint)"], weak: ["var(--gold)", "#f6eede"],
    deceptive: ["var(--deceptive)", "var(--deceptive-tint)"] };
  return map[v] ?? ["var(--neutral)", "var(--neutral-tint)"];
}

/* ---- annotators (the LLM panel; humans verify its tags rather than authoring their own) ---- */
const annotators = () => EVAL.annotators || [];
const annotatorColor = idx => MEMBER_COLORS[idx % MEMBER_COLORS.length];
const annotatorLabel = idx => (annotators().find(a => a.index === idx) || {}).label || "evaluator";
const annotatorBadge = idx => annotatorLabel(idx)[0].toUpperCase();

/* every tag from every panel member, flattened and stamped with who authored it */
function allTags() {
  const out = [];
  (EVAL.members || []).forEach(m => (m.tags || []).forEach(t => out.push({ ...t, annotator: m.index })));
  return out;
}
function tagVisible(t) {
  return state.filters.axes.has(axisOf(t.behavior)) && t.confidence >= state.filters.minConf
    && state.filters.annotators.has(t.annotator);
}

/* ---- trace spans grouped by turn (rebuilt on every evaluation refresh, so the trace grows live) ---- */
let spanById = new Map(), spansByTurn = new Map(), turns = [], actorByTurn = {}, lastNoteSpanByPlayer = {};
function rebuildTrace() {
  spanById = new Map((EVAL.spans || []).map(s => [s.span, s]));
  spansByTurn = new Map();
  (EVAL.spans || []).forEach(s => { if (!spansByTurn.has(s.turn)) spansByTurn.set(s.turn, []); spansByTurn.get(s.turn).push(s); });
  turns = [...spansByTurn.keys()].sort((a, b) => a - b);
  // the acting player of a turn is whoever the engine had take the board actions — not whoever
  // happened to speak first during negotiation (which was pinning every card to one player)
  actorByTurn = {};
  steps.forEach(s => { if (s.current_player != null) actorByTurn[s.turn] = s.current_player; });
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
/* a short headline for the collapsed card: prefer a win/elimination, else the actor's first message, else its first action */
function turnSummary(turn) {
  const ls = spansByTurn.get(turn) || [];
  const win = ls.find(s => s.layer === "event" && /WON|ELIMINATED/i.test(s.text));
  if (win) return win.text;
  const msg = ls.find(s => s.layer === "message");
  if (msg) return msg.text.replace(/^[^:]*:\s*/, "");
  const act = ls.find(s => s.layer === "action");
  return act ? act.text : "—";
}

/* ---- reader state ---- */
const state = {
  expanded: new Set(), openAnno: new Set(), openEvidence: new Set(), mode: "read", focusTurn: turns[0] ?? 0,
  filters: {
    axes: new Set(["skill", "conduct", "affect"]), minConf: 1,
    annotators: new Set(annotators().map(a => a.index)),
    showPrivate: true, showCommentary: true,
  },
  drawer: null,
};
// annotators the filter set has already decided on; lets a panel that finishes after page
// load default to visible without re-enabling members the viewer explicitly hid
const knownAnnotators = new Set(state.filters.annotators);

