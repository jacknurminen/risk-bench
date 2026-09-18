/* Keep the board and a scrollable timeline visible on desktop and laptop screens. */
(() => {
  const feed = document.querySelector('.feed'), header = document.querySelector('.masthead');
  const divider = document.getElementById('mapDivider'), pane = document.getElementById('timelinePane');
  const button = document.getElementById('mapFocus'), board = document.querySelector('.boardcard');
  if (!divider || !pane || !button) return;
  const media = matchMedia('(min-width: 1000px) and (min-height: 600px)');
  const analysis = document.getElementById('readerAnalysis');
  if (analysis && media.matches) analysis.open = false;
  const key = 'risk-bench:map-split';
  let ratio = 0.6, focused = false, drag = null, limits = {min: 0, max: 0};
  try {
    const saved = Number(localStorage.getItem(key));
    if (saved >= 0.25 && saved <= 0.85) ratio = saved;
  } catch (_) { /* The layout also works without browser storage. */ }
  function save() { try { localStorage.setItem(key, String(ratio)); } catch (_) {} }
  function layout() {
    document.body.classList.toggle('readerSplit', media.matches);
    document.body.classList.toggle('mapFocused', media.matches && focused);
    document.documentElement.style.setProperty('--reader-header-height', `${header.offsetHeight}px`);
    button.textContent = focused ? 'Show timeline' : 'Focus map';
    button.setAttribute('aria-pressed', String(focused));
    if (!media.matches) return;
    const height = feed.clientHeight;
    // Account for wrapped playback buttons and player statistics, as well as the map itself.
    const chrome = board.offsetHeight - document.getElementById('board').offsetHeight;
    limits.max = height - divider.offsetHeight - 180;
    limits.min = Math.min(limits.max, chrome + 180);
    const size = Math.round(Math.max(limits.min, Math.min(limits.max, height * ratio)));
    feed.style.setProperty('--map-pane-height', `${size}px`);
    divider.setAttribute('aria-valuemin', String(Math.round(limits.min / height * 100)));
    divider.setAttribute('aria-valuemax', String(Math.round(limits.max / height * 100)));
    divider.setAttribute('aria-valuenow', String(Math.round(size / height * 100)));
    divider.setAttribute('aria-valuetext', `${Math.round(size / height * 100)} percent map`);
  }
  function focusMap(value) { focused = value; layout(); }
  window.readerLayout = {focusMap};
  button.addEventListener('click', () => focusMap(!focused));
  divider.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    drag = {y: event.clientY, height: board.offsetHeight};
    divider.setPointerCapture(event.pointerId);
    divider.focus();
    document.body.classList.add('resizingMap');
    event.preventDefault();
  });
  divider.addEventListener('pointermove', event => {
    if (!drag) return;
    ratio = Math.max(limits.min, Math.min(limits.max, drag.height + event.clientY - drag.y)) / feed.clientHeight;
    layout();
  });
  divider.addEventListener('lostpointercapture', () => {
    drag = null;
    document.body.classList.remove('resizingMap');
    save();
  });
  divider.addEventListener('dblclick', () => { ratio = 0.6; save(); layout(); });
  divider.addEventListener('keydown', event => {
    const delta = {ArrowUp: -24, ArrowDown: 24}[event.key];
    if (delta === undefined && !['Home', 'End', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const size = event.key === 'Home' ? limits.min : event.key === 'End' ? limits.max
      : event.key === 'Enter' ? feed.clientHeight * 0.6 : board.offsetHeight + delta;
    ratio = Math.max(limits.min, Math.min(limits.max, size)) / feed.clientHeight;
    save(); layout();
  });
  media.addEventListener('change', () => { focused = false; layout(); });
  window.addEventListener('resize', layout);
  const observer = new ResizeObserver(layout);
  [header, feed, board.querySelector('.controls'), board.querySelector('.statstrip')].forEach(node => observer.observe(node));
  layout();
})();
