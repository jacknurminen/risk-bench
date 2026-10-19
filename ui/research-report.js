function researchMethodLabel(method) { return method === "live_rounds" ? "Live round evaluation" : "Whole-game analysis"; }
/* Shared presentation for single-game and tournament research summaries. */
const researchEscape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const researchCounts = counts => Object.entries(counts || {}).map(([key, count]) =>
  researchEscape(key.replaceAll('_', ' ')) + ': ' + count).join(' · ') || 'None';
const researchOutcomes = [['fulfilled','Fulfilled'], ['violated','Violated'], ['unresolved','Unresolved'],
  ['closed','Closed'], ['agreed','Pending (agreed)'], ['committed','Pending (unilateral)'], ['made','Pending (legacy)']];
function researchTable(headers, rows) {
  return '<div class="researchScroll"><table class="researchReportTable"><thead><tr>' +
    headers.map(h => '<th scope="col">' + researchEscape(h) + '</th>').join('') + '</tr></thead><tbody>' +
    rows.map(cells => '<tr>' + cells.map((c,i) => i ? '<td>' + c + '</td>' : '<th scope="row">' + c + '</th>').join('') +
      '</tr>').join('') + '</tbody></table></div>';
}
function researchEvidenceLink(record, kind, id, label, local = false) {
  const key = record.index + ':' + kind + ':' + id;
  if (local) return '<button class="researchEvidence" onclick="' + researchEscape(
    'closeStats(); openUnitDrawer(' + JSON.stringify(key) + ')') + '">' + researchEscape(label) + '</button>';
  const query = new URLSearchParams({version: record.version || ''});
  return '<a class="researchEvidence" href="/games/' + encodeURIComponent(record.game_id) + '?' + query +
    '#unit=' + encodeURIComponent(key) + '">' + researchEscape(label) + '</a>';
}
function researchSourceLink(record, span, local = false) {
  const label = 'Message [' + span + ']';
  if (local) return '<button class="researchEvidence" onclick="closeStats(); openLineDrawer(' + Number(span) + ')">' + label + '</button>';
  return '<a class="researchEvidence" href="/games/' + encodeURIComponent(record.game_id) + '?' +
    new URLSearchParams({version: record.version || ''}) + '#span=' + Number(span) + '">' + label + '</a>';
}
function researchPlayerDetails(row, local = false) {
  return '<details class="researchRecordDetails"><summary>' + researchEscape(row.model) +
    ' — evidence, reasons, roles and exclusions</summary>' + row.records.map(record => {
      const p = record.player;
      const prefix = '<h5>' + researchEscape(record.game_id + ' · ' + p.name) + '</h5>';
      const commitments = p.commitment_records.map(c => '<li>' + researchEvidenceLink(record, 'commitment', c.id,
        c.status + ' · ' + c.terms, local) + (c.reason ? '<p>' + researchEscape(c.reason) + '</p>' : '') +
        '<small>' + (c.established ? 'Established' : 'Never established') + ' · ' + researchEscape(c.provenance) +
        (c.agreement_id ? ' · arrangement ' + researchEscape(c.agreement_id) : '') + '</small></li>').join('');
      const deception = p.deception_records.map(d => '<li>' + researchEscape(d.proposition) + ' — ' +
        d.ids.map(id => researchEvidenceLink(record, d.kind || 'statement', id, 'Message and private evidence ↗', local)).join(' · ') + '</li>').join('');
      const exclusions = p.excluded_records.map(u => '<li>' + researchEvidenceLink(record, u.kind, u.id, u.id, local) +
        ': ' + researchEscape(u.issues.join('; ') || 'Invalid or needs review') + '</li>').join('');
      const coverage = p.coverage_records.map(c => '<li>' + researchSourceLink(record, c.span, local) + ' · ' + researchEscape(c.status) +
        ': ' + researchEscape(c.reason) + '</li>').join('');
      return prefix + '<p>Participant roles: ' + (p.role_records.map(r => researchEscape(r.role.replaceAll('_',' ') +
        ' · ' + r.agreement_id)).join('; ') || 'No documented shared-arrangement role') + '</p>' +
        '<h6>Commitments and proposals</h6>' + (commitments ? '<ul>' + commitments + '</ul>' : '<p>None recorded.</p>') +
        '<h6>Deception evidence</h6>' + (deception ? '<ul>' + deception + '</ul>' : '<p>None recorded.</p>') +
        (p.candidate_records?.length ? '<h6>Candidate reviews — not findings</h6>' + p.candidate_records.map(c =>
          '<p>' + researchSourceLink(record,c.passage.span,local) + ' · ' + researchEscape(c.proposition) + '</p>' +
          c.review_history.map(r => '<p>' + researchEscape(r.outcome + ': ' + r.reason) + '</p>').join('')).join('') : '') +
        '<h6>Excluded assessments</h6>' + (exclusions ? '<ul>' + exclusions + '</ul>' : '<p>None.</p>') +
        '<h6>Missing or uncertain screening</h6>' + (coverage ? '<ul>' + coverage + '</ul>' : '<p>None.</p>');
    }).join('') + '</details>';
}
function researchCohortHtml(cohort, local = false) {
  const modern = cohort.scope === 'commitment_communication';
  const players = cohort.players;
  const maxEstablished = Math.max(1, ...players.map(p => p.established));
  const bars = players.map(p => '<div class="researchOutcomeRow"><span>' + researchEscape(p.model) +
    (p.kind === 'human' ? ' (human)' : '') + '</span><strong>' + p.established + '</strong><div class="researchOutcomeTrack">' +
    researchOutcomes.map(([key,label]) => p.outcomes[key] ? '<span class="researchOutcome ' + key + '" style="width:' +
      (100 * p.outcomes[key] / maxEstablished) + '%" title="' + label + ': ' + p.outcomes[key] + '"></span>' : '').join('') +
    '</div></div>').join('');
  const table = researchTable(['Model / actor type', 'Established obligations', 'Never established', 'Fulfilled', 'Violated',
    'Unresolved', 'Closed', 'Pending', modern ? 'Documented deception incidents' : 'Historical deception incidents', 'Games with incidents / observed'], players.map(p => [
      researchEscape(p.model) + '<small>' + researchEscape(p.kind) + '</small>', p.established, p.unestablished,
      p.outcomes.fulfilled || 0, p.outcomes.violated || 0, p.outcomes.unresolved || 0, p.outcomes.closed || 0,
      (p.outcomes.agreed || 0) + (p.outcomes.committed || 0) + (p.outcomes.made || 0), p.deception_incidents,
      p.observed_games.length ? p.deception_games.length + ' / ' + p.observed_games.length : 'Unavailable',
    ]));
  const coverage = researchTable(['Model', 'Messages', 'Turns survived', 'Screened / messages', 'Unclear', 'Missing',
    'Outside cutoff / source mismatch', 'Excluded units', 'Initiator / accepting / unknown', 'Adherence'], players.map(p => {
      const c = p.coverage, a = p.adherence;
      return [researchEscape(p.model), p.messages, p.turns_survived,
        ((c.candidate || 0) + (c.no_candidate || 0) + (c.unclear || 0)) + ' / ' + p.messages,
        c.unclear || 0, c.missing || 0, (c.outside_cutoff || 0) + (c.source_mismatch || 0), p.excluded,
        [p.roles.initiator || 0, p.roles.accepting_participant || 0, p.roles.unknown || 0].join(' / '),
        a.resolved ? a.fulfilled + ' / ' + a.resolved + ' (' + Math.round(100 * a.fulfilled / a.resolved) + '%)' : 'Unavailable'];
    }));
  return '<article class="researchCohort"><header><p class="researchOverline">Evaluator ' + researchEscape(cohort.evaluator) +
    ' · replicate ' + cohort.replicate + '</p><h3>Commitments and documented evidence</h3><p>' +
    researchEscape(researchMethodLabel(cohort.method)) + ' · ' + researchEscape(cohort.codebook_version || 'Unknown codebook') + ' · ' + cohort.games.length + ' game(s)</p></header>' +
    (modern ? '<p class="researchReportNote">Thesis v4: commitments and documented deception in commitment-related communication. ' +
    'Contradiction is supporting evidence within a deception assessment. Candidate reviews remain separate from findings.</p>' :
    '<p class="researchScopeNotice">Historical statement schema. Deceptive assessments below retain their original scope; ' +
    'they have not been reassessed under the October commitment-related deception rules. Generic statement classifications are not research outcomes here. ' +
    'In-scope message counts and separate deception-candidate review outcomes were not collected by this instrument.</p>') +
    '<div class="researchOutcomeChart" role="group" aria-label="Established commitment outcomes by model">' + bars +
    '<p class="researchOutcomeLegend">' + researchOutcomes.map(([key,label]) => '<span><i class="researchOutcome ' + key + '"></i>' + label + '</span>').join('') + '</p></div>' +
    table + '<h4>Exposure, screening and review</h4>' + coverage +
    (modern ? researchTable(['Model', 'In-scope messages', 'Out of scope', 'Scope unclear / unavailable',
      'Messages missing evidence', 'Candidates pending / excluded / documented'], players.map(p => [researchEscape(p.model),
      p.screening_scope?.in_scope || 0, p.screening_scope?.out_of_scope || 0,
      (p.screening_scope?.unclear || 0) + (p.screening_scope?.unavailable || 0), p.missing_evidence_messages || 0,
      ['pending','excluded','documented'].map(k => p.candidate_reviews?.[k] || 0).join(' / ')])) : '') +
    '<p class="researchReportNote">Counts are actor obligations, not shared arrangements. Never-established entries include pending, rejected or unresolved proposals. ' +
    (modern ? 'Participant roles count distinct arrangements and use explicitly recorded participant histories. ' :
    'Participant roles count distinct arrangements and are inferred only from recorded proposal/agreement histories; missing roles stay unknown. ') +
    'Turns survived count game turns alive, including the elimination turn and excluding setup. Outcome totals include all recorded action origins; the breakdown is below. Adherence uses valid, established, agent-choice fulfilled / (fulfilled + violated) outcomes.</p>' +
    '<details class="researchRecordDetails"><summary>Observation and evaluator coverage</summary>' +
    researchTable(['Game', 'Evaluator status', 'Source', 'Observation'], cohort.games.map(g => [researchEscape(g.id),
      researchEscape(g.status), g.source_matches ? 'Matches' : 'Mismatch: excluded', g.partial ? 'Partial' : 'Full recorded prefix'])) +
    cohort.games.map(g => (g.unassigned_exclusions || []).map(u => '<p>Excluded unit ' + researchEscape(u.id) +
      ' in ' + researchEscape(g.id) + ': unknown actor ' + researchEscape(u.actor) + '. ' + researchEscape(u.issues.join('; ')) + '</p>').join('')).join('') +
    '<p>Instrument hash: ' + researchEscape(cohort.codebook_hash || 'Unknown') + '</p>' +
    players.map(p => '<p>' + researchEscape(p.model) + ' · configurations: ' + researchEscape(p.specs.join(', ')) +
      ' · outcome provenance: ' + researchCounts(p.provenance) + ' · screening: ' + researchCounts(p.coverage) + '</p>').join('') +
    '</details>' + players.map(p => researchPlayerDetails(p, local)).join('') + '</article>';
}
function researchGameContextHtml(games) {
  return '<details class="researchRecordDetails"><summary>Game setting, missions and survival</summary>' + games.map(game => {
    const c = game.context;
    if (!c) return '';
    return '<h4>' + researchEscape(game.id) + '</h4><p>Winner: ' + researchEscape(game.winner || (game.finished ? 'None' : 'Not finished')) +
      ' · ' + c.turns_played + ' turns · ' + c.rounds_played + ' rounds reached · ' + researchEscape(c.termination_reason) +
      (c.related_branch ? ' · Related branch (shared history)' : '') + '</p>' +
      researchTable(['Player', 'Model', 'Mission', 'Messages', 'Turns survived', 'Elimination'], c.players.map(p => [
        researchEscape(p.name), researchEscape(p.model), researchEscape(p.mission), p.messages, p.turns_survived,
        p.eliminated_turn == null ? 'Not observed' : 'Turn ' + p.eliminated_turn + ' · round ' + p.eliminated_round]));
  }).join('') + '</details>';
}
function researchIntroHtml() {
  return '<p class="researchReportNote">Each evaluator and codebook version is reported separately. Counts never pool evaluator votes. ' +
    'Games with incidents use source-matched observations; partial assessments remain provisional. Human records remain separate. Missing screening and untagged messages do not establish honesty. ' +
    'Citation checks verify sources, not interpretations. Related branches share history and are not independent observations.</p>';
}
function renderResearchGameReport(report, game) {
  const context = game.research_context;
  const cohorts = report.members.map(m => ({method: report.method, codebook_version: report.codebook_version, codebook_hash: report.codebook_hash,
    scope: report.scope,
    evaluator: m.label, replicate: m.index + 1,
    games: [{id: EVAL.game_id, status: m.status, partial: report.partial, source_matches: report.source_matches, unassigned_exclusions: m.unassigned_exclusions}],
    players: m.players.map(p => ({...p, model: p.model === p.name ? p.model : p.model + ' · ' + p.name, specs: [p.spec],
      turns_survived: context?.players.find(c => c.name === p.name)?.turns_survived ?? 'Unknown',
      games: [EVAL.game_id], observed_games: report.source_matches ? [EVAL.game_id] : [], deception_games: p.deception_incidents ? [EVAL.game_id] : [],
      records: [{game_id: EVAL.game_id, version: report.version, index: m.index, player: p}]}))}));
  return researchIntroHtml() + cohorts.map(c => researchCohortHtml(c, true)).join('') +
    researchGameContextHtml([{id: EVAL.game_id, context, winner: game.result?.winner, finished: !!game.result}]);
}
function tournamentOutputHtml(output) {
  if (!output?.players?.length) return '';
  const rows = output.players;
  const colors = ['#a33f3b', '#3670ab', '#6e8f3a', '#71509c', '#c97a29', '#1f92ab'];
  const number = (value, decimals = 0) => value == null ? '—' :
    value.toLocaleString('en-US', {maximumFractionDigits: decimals});
  const charts = [
    ['messages_per_game', 'Messages per game', 'msg / game', 2],
    ['messages_per_turn', 'Messages per turn', 'msg / turn', 2],
    ['words_per_message', 'Average message length', 'words / msg', 1],
    ['thoughts_per_game', 'Reasoning entries per game', 'entries / game', 2],
    ['thoughts_per_turn', 'Reasoning entries per turn', 'entries / turn', 2],
    ['words_per_thought', 'Average reasoning length', 'words / entry', 1],
  ].map(([key, title, unit, decimals]) => {
    const max = Math.max(1, ...rows.map(row => row[key] ?? 0));
    return '<figure class="researchOutputChart"><figcaption>' + title + '</figcaption>' + rows.map((row, i) => {
      const value = row[key], color = colors[i % colors.length];
      return '<div class="researchOutcomeRow"><span class="researchOutputPlayer" style="color:' + color + '">' +
        researchEscape(row.name) + '</span><strong>' +
        (value == null ? 'No entries' : number(value, decimals) + ' ' + unit) +
        '</strong><div class="researchOutcomeTrack" aria-hidden="true"><span style="background:' + color +
        ';width:' + ((value ?? 0) / max * 100) + '%"></span></div></div>';
    }).join('') + '</figure>';
  }).join('');
  return '<section class="researchCohort" aria-label="Player output volume"><header><h3>Player output volume</h3>' +
    '<p>' + number(output.game_count) + ' recorded game(s) · ' + number(output.unfinished_games) +
    ' unfinished · includes games without evaluations</p></header>' +
    '<p class="researchReportNote">Public and private messages sent, and recorded internal reasoning entries. ' +
    'Lengths count whitespace-separated words; notes, tool calls and hidden provider reasoning are excluded. ' +
    'Per-game averages include silent games. Per-turn averages divide pooled counts by recorded game turns' +
    (output.includes_setup ? ', including setup once per game' : '') +
    ', including silent turns and turns after elimination. Average lengths divide total words by total entries. ' +
    'Unfinished games contribute their recorded prefix; each player’s denominators are shown below.</p>' +
    '<div class="researchOutputCharts">' + charts + '</div>' +
    '<details class="researchRecordDetails"><summary>Output totals and denominators</summary>' +
    researchTable(['Player', 'Games', 'Recorded turns', 'Messages', 'Public', 'Private', 'Message words',
      'Reasoning entries', 'Reasoning words'], rows.map(row => [researchEscape(row.name),
        ...['games', 'recorded_turns', 'messages', 'public_messages', 'private_messages', 'message_words',
          'thoughts', 'thought_words'].map(key => number(row[key]))])) + '</details></section>';
}
function renderTournamentResearch(report, games, output) {
  return tournamentOutputHtml(output) + researchIntroHtml() +
    (report?.cohorts.length ? report.cohorts.map(c => researchCohortHtml(c)).join('') :
    '<p class="researchScopeNotice">No research evaluations are available. Missing evaluations are not zero findings.</p>') +
    '<details class="researchRecordDetails" open><summary>Evaluation availability by game</summary>' +
    researchTable(['Game', 'Method', 'Evaluation', 'Instrument', 'Branch'], (report?.availability || []).map(g => [
      '<a href="/games/' + encodeURIComponent(g.game_id) + '?' + new URLSearchParams({version: g.version || ''}) + '">' + researchEscape(g.game_id) + '</a>', researchEscape(researchMethodLabel(g.method)), researchEscape(g.status),
      researchEscape(g.codebook_version || 'None'), g.related_branch ? 'Related branch' : 'Original'])) + '</details>' +
    researchGameContextHtml(games);
}
