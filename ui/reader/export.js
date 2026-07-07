/* ================= IMAGE EXPORT ================= */
/* Report figures straight from the reader: an export mode where page blocks (trace lines, turn
   cards, charts, the board…) are click-selected and then rasterized together into one PNG.
   Browsers have no DOM-screenshot API, so the selection is cloned with every computed style
   inlined, wrapped in an <svg><foreignObject>, drawn onto a canvas at up to 2x, and downloaded.
   The webfonts are fetched and embedded as data: URIs so figures keep the theme's type; offline
   (e.g. the standalone export file) they gracefully fall back to system fonts. */

const EXPORTABLE = [
  ".pill", ".traceline", ".tpills", ".tcard",             // timeline
  ".pcard", ".synthCard", "#synthSection", ".matrix",     // rail · synthesis · compare
  ".chart", "#tokenChart", "#statsTable",                 // stats
  "#board", ".statstrip", ".boardcard",                   // board
].join(", ");

let exporting = false;
let exportHovered = null;
const exportSelection = new Set();

function toggleExport() {
  pruneExportSelection();
  if (!exporting) { setExportMode(true); return; }
  if (exportSelection.size) { openExportModal(); return; }
  setExportMode(false);
}

function setExportMode(on) {
  exporting = on;
  document.body.classList.toggle("exporting", on);
  if (!on) {
    exportSelection.forEach(node => node.classList.remove("exportSel"));
    exportSelection.clear();
    clearExportHover();
  }
  renderExportButton();
}

function renderExportButton() {
  const button = el("exportBtn");
  button.classList.toggle("on", exporting);
  button.textContent = exporting
    ? (exportSelection.size ? `✂ save ${exportSelection.size} selected` : "✂ click blocks…")
    : "✂ export";
}

/* selection: capture-phase so the blocks' own onclick handlers (expand, drawer, board
   winding…) never fire while export mode is on; clicks that hit no exportable block pass
   through untouched, which keeps the masthead and the stats modal's controls usable */
function exportTargetOf(eventTarget) {
  if (!(eventTarget instanceof Element) || eventTarget.closest("#exportModal")) return null;
  return eventTarget.closest(EXPORTABLE);
}

document.addEventListener("click", event => {
  if (!exporting || exportModalOpen()) return;
  const target = exportTargetOf(event.target);
  if (!target) return;
  event.preventDefault(); event.stopPropagation();
  if (exportSelection.has(target)) { exportSelection.delete(target); target.classList.remove("exportSel"); }
  else { exportSelection.add(target); target.classList.add("exportSel"); }
  renderExportButton();
}, true);

document.addEventListener("mouseover", event => {
  if (!exporting || exportModalOpen()) { clearExportHover(); return; }
  const target = exportTargetOf(event.target);
  if (target === exportHovered) return;
  clearExportHover();
  if (target) { exportHovered = target; target.classList.add("exportHover"); }
}, true);

document.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !exporting) return;
  if (exportModalOpen()) { closeExportModal(); return; }
  if (statsOpen() || el("notesModal").classList.contains("open") || state.drawer) return;
  setExportMode(false);
});

function clearExportHover() {
  if (exportHovered) exportHovered.classList.remove("exportHover");
  exportHovered = null;
}

/* a live re-render can replace selected nodes with detached copies; drop those */
function pruneExportSelection() {
  exportSelection.forEach(node => { if (!node.isConnected) exportSelection.delete(node); });
}

/* ---- name popup ---- */
function exportModalOpen() { return el("exportModal").classList.contains("open"); }

function openExportModal() {
  el("exportMeta").textContent = `${exportSelection.size} block${exportSelection.size === 1 ? "" : "s"} selected`;
  const nameInput = el("exportName");
  if (!nameInput.value) nameInput.value = defaultExportName();
  el("exportModal").classList.add("open");
  nameInput.focus(); nameInput.select();
}

function closeExportModal() { el("exportModal").classList.remove("open"); }

function defaultExportName() {
  const turns = [...exportSelection].map(node => node.closest("[data-turn]")?.dataset.turn).filter(Boolean);
  const unique = [...new Set(turns)];
  return unique.length === 1 ? `riskbench-turn-${unique[0].padStart(2, "0")}` : "riskbench-export";
}

async function saveExport() {
  pruneExportSelection();
  if (!exportSelection.size) { toast("nothing left selected — the page re-rendered"); closeExportModal(); return; }
  const name = (el("exportName").value.trim() || "export").replace(/\.png$/i, "");
  const button = el("exportSave");
  button.disabled = true; button.textContent = "rendering…";
  try {
    const blob = await renderSelectionToPng([...exportSelection]);
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `${name}.png`;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
    toast(`saved ${name}.png`);
    el("exportName").value = "";
    closeExportModal();
    setExportMode(false);
  } catch (error) {
    console.error("image export failed", error);
    toast("export failed — see the console");
  } finally {
    button.disabled = false; button.textContent = "Save PNG";
  }
}

/* ---- rasterizer: clones → inline styles → SVG foreignObject → canvas → PNG ---- */
async function renderSelectionToPng(nodes) {
  nodes.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
  // the selection outline lives in computed styles too — strip it before cloning, restore after
  nodes.forEach(node => node.classList.remove("exportSel", "exportHover"));
  const wrapper = document.createElement("div");
  wrapper.style.cssText = "display:flex;flex-direction:column;align-items:flex-start;gap:12px;padding:18px;" +
    `width:max-content;background:${getComputedStyle(document.body).backgroundColor};`;
  nodes.forEach(node => wrapper.appendChild(cloneWithComputedStyles(node)));
  nodes.forEach(node => node.classList.add("exportSel"));

  const stage = document.createElement("div");
  stage.style.cssText = "position:fixed;left:-100000px;top:0;";
  stage.appendChild(wrapper);
  document.body.appendChild(stage);
  const width = Math.ceil(wrapper.getBoundingClientRect().width);
  const height = Math.ceil(wrapper.getBoundingClientRect().height);
  wrapper.style.width = `${width}px`;
  const xml = new XMLSerializer().serializeToString(wrapper);
  stage.remove();

  const fontCss = await embeddedFontCss();
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
    (fontCss ? `<style>${fontCss}</style>` : "") +
    `<foreignObject width="100%" height="100%">${xml}</foreignObject></svg>`;
  // a data: URI, not a blob URL — Chromium's same-origin check for SVG images with
  // foreignObject rejects blob-loaded ones, which taints the canvas and blocks toBlob
  const image = new Image();
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  await image.decode();

  const scale = Math.min(2, 16000 / width, 16000 / height);  // stay under the canvas dimension cap
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d");
  context.scale(scale, scale);
  context.drawImage(image, 0, 0);
  return await new Promise((resolve, reject) =>
    canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("canvas.toBlob produced nothing")), "image/png"));
}

function cloneWithComputedStyles(source) {
  const clone = source.cloneNode(true);
  const pairs = [[source, clone]];
  while (pairs.length) {
    const [from, to] = pairs.pop();
    inlineComputedStyle(from, to);
    if (from instanceof HTMLInputElement) to.setAttribute("value", from.value);
    for (let i = 0; i < from.children.length; i++) pairs.push([from.children[i], to.children[i]]);
  }
  return clone;
}

function inlineComputedStyle(from, to) {
  const computed = getComputedStyle(from);
  let css = "";
  for (let i = 0; i < computed.length; i++) {
    const property = computed[i];
    const value = computed.getPropertyValue(property);
    // a url() reference to anything but a data: URI taints the canvas (and would not
    // load inside an SVG image anyway), so drop such properties from the figure
    if (value.includes("url(") && !value.includes("data:")) continue;
    css += `${property}:${value};`;
  }
  to.setAttribute("style", css);
}

/* fonts must live inside the SVG as data: URIs — an SVG rendered as an image cannot make
   network requests, and cross-origin resources would taint the canvas anyway */
let fontCssCache = null;
async function embeddedFontCss() {
  if (fontCssCache !== null) return fontCssCache;
  try {
    const link = document.querySelector('link[href*="fonts.googleapis.com/css"]');
    if (!link) return (fontCssCache = "");
    let css = await (await fetch(link.href)).text();
    const urls = [...new Set([...css.matchAll(/url\(\s*['"]?(https:[^)'"]+)['"]?\s*\)/g)].map(match => match[1]))];
    const dataUrls = await Promise.all(urls.map(async url => {
      const blob = await (await fetch(url)).blob();
      return await new Promise(resolve => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.readAsDataURL(blob);
      });
    }));
    urls.forEach((url, index) => { css = css.replaceAll(url, dataUrls[index]); });
    // any external reference that slipped through would taint the canvas; system fonts beat no export
    fontCssCache = /url\(\s*['"]?http/.test(css) ? "" : css;
  } catch {
    toast("webfonts unavailable — exporting with system fonts");
    fontCssCache = "";
  }
  return fontCssCache;
}

el("exportName").addEventListener("keydown", event => { if (event.key === "Enter") saveExport(); });