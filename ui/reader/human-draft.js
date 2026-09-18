/* A local plan never mutates the game or the reader's historical snapshots. */
class HumanTurnDraft {
  constructor(request, previous = null) {
    this.request = request;
    this.actions = request.actions.map((action, action_index) => ({...action, action_index}));
    this.modes = [...new Set(this.actions.map(a => a.kind))];
    this.mode = ["negotiation", "notes"].includes(request.kind) ? request.kind
      : ["place_armies", "attack", "fortify", "trade_cards"].find(kind => this.modes.includes(kind)) || this.modes[0];
    this.allocations = {}; this.history = []; this.source = ""; this.target = ""; this.count = 1;
    this.tradeIndex = this.actions.find(a => a.kind === "trade_cards")?.action_index ?? null;
    if (previous?.mode === "attack" && this.mode === "attack"
        && previous.request.view.turn_number === request.view.turn_number) {
      if (this.pairs.some(a => a.from_territory === previous.source)) this.source = previous.source;
      if (this.pairs.some(a => a.from_territory === this.source && a.to_territory === previous.target))
        this.target = previous.target;
    }
  }
  get placements() { return this.actions.filter(a => a.kind === "place_armies"); }
  get total() { return Object.values(this.allocations).reduce((sum, n) => sum + n, 0); }
  get available() { return this.placements[0]?.count ?? 0; }
  get remaining() { return this.available - this.total; }
  get pairs() { return this.actions.filter(a => a.kind === this.mode && a.from_territory); }
  get pair() { return this.pairs.find(a => a.from_territory === this.source && a.to_territory === this.target); }
  setMode(mode) {
    if (!this.modes.includes(mode)) return;
    this.mode = mode; this.source = ""; this.target = ""; this.count = 1;
  }
  allocate(territory, count) {
    if (this.mode !== "place_armies" || !this.placements.some(a => a.territory === territory)
        || !Number.isInteger(count) || count < 0) return false;
    const old = this.allocations[territory] || 0;
    if (count === old || count - old > this.remaining) return false;
    this.history.push({...this.allocations});
    if (count) this.allocations[territory] = count; else delete this.allocations[territory];
    return true;
  }
  add(territory, count = 1) { return this.allocate(territory, (this.allocations[territory] || 0) + count); }
  undo() { if (this.history.length) this.allocations = this.history.pop(); }
  clear() { if (this.total) { this.history.push({...this.allocations}); this.allocations = {}; } }
  select(territory) {
    if (this.mode === "place_armies") return this.add(territory);
    if (this.pairs.some(a => a.from_territory === this.source && a.to_territory === territory)) {
      this.target = territory; this.count = this.pair.count ?? 1; return true;
    }
    return this.selectSource(territory);
  }
  selectSource(territory) {
    if (!this.pairs.some(a => a.from_territory === territory)) return false;
    this.source = territory; this.target = ""; this.count = 1; return true;
  }
  setCount(count) {
    if (this.mode !== "fortify" || !this.pair || !Number.isInteger(count) || count < 1 || count > this.pair.count)
      return false;
    this.count = count; return true;
  }
  get ready() {
    if (this.mode === "place_armies") return this.total > 0 && this.remaining === 0;
    if (["attack", "fortify"].includes(this.mode)) return !!this.pair;
    return ["negotiation", "notes"].includes(this.mode) || this.modes.includes(this.mode);
  }
  get preview() {
    if (this.mode === "place_armies") return {...this.allocations};
    if (this.mode === "fortify" && this.pair) return {[this.source]: -this.count, [this.target]: this.count};
    return {};
  }
  response() {
    if (!this.ready) throw new Error("Complete your plan before committing.");
    if (["negotiation", "notes"].includes(this.mode)) return {};
    if (this.mode === "place_armies") return {placements: this.placements
      .filter(a => this.allocations[a.territory])
      .map(a => ({action_index: a.action_index, count: this.allocations[a.territory]}))};
    if (this.mode === "trade_cards") return {action_index: this.tradeIndex};
    if (this.mode === "fortify") return {action_index: this.pair.action_index, count: this.count};
    return {action_index: (this.pair || this.actions.find(a => a.kind === this.mode)).action_index};
  }
}
if (typeof module !== "undefined") module.exports = HumanTurnDraft;
