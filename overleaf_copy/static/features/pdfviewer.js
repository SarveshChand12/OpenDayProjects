/* PDF.js-based viewer.
 * - Keeps your scroll position when the PDF is recompiled.
 * - Double-click anywhere in the PDF to jump to that source line (reverse SyncTeX).
 * - "→ PDF" jumps to and highlights the cursor's location (forward SyncTeX).
 * - Zoom (buttons, Ctrl+wheel, fit width) is remembered; optional dark PDF.
 */
(() => {
  const OC = window.OC;
  if (!window.pdfjsLib) { console.warn("pdf.js not loaded; using the browser PDF viewer"); return; }
  pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.js";

  const GAP = 12;          // px between pages
  const container = document.querySelector("#pdfContainer");
  const ui = OC.el(`<div class="pdfjs">
      <div class="pdfjs-bar">
        <button data-act="out" title="Zoom out">−</button>
        <select data-act="zoom" title="Zoom">
          <option value="fit">Fit width</option><option value="page">Fit page</option>
          <option value="0.5">50%</option><option value="0.75">75%</option><option value="1">100%</option>
          <option value="1.25">125%</option><option value="1.5">150%</option><option value="2">200%</option><option value="3">300%</option>
          <option value="custom" hidden></option>
        </select>
        <button data-act="in" title="Zoom in">＋</button>
        <span class="sep"></span>
        <button data-act="prev" title="Previous page">▲</button>
        <input data-act="page" type="number" min="1" value="1" title="Page">
        <span class="count">/ 0</span>
        <button data-act="next" title="Next page">▼</button>
        <span class="sep"></span>
        <button data-act="dark" title="Dark PDF (invert colours)">🌙</button>
        <span class="hint muted">Double-click the PDF to jump to the source</span>
      </div>
      <div class="pdfjs-scroll"><div class="pdfjs-pages"></div></div>
    </div>`);
  ui.hidden = true;
  container.appendChild(ui);
  const scroller = ui.querySelector(".pdfjs-scroll");
  const zoomSel = ui.querySelector('[data-act="zoom"]');
  const pageInput = ui.querySelector('[data-act="page"]');

  let pdf = null;              // PDFDocumentProxy
  let pageSizes = [];          // [{w,h}] at scale 1 (PDF points)
  let zoomMode = localStorage.getItem("oc-pdf-zoom") || "fit";
  let scale = 1;
  let pagesEl = ui.querySelector(".pdfjs-pages");
  let observer = null;
  let loadSeq = 0;       // cancels superseded document loads
  let renderGen = 0;     // cancels superseded page renders (zoom changes)
  let loading = false;
  let rerenderPending = false;
  let dark = localStorage.getItem("oc-pdf-dark") === "1";
  ui.classList.toggle("dark-pdf", dark);

  function computeScale() {
    if (!pageSizes.length) return 1;
    const avail = scroller.clientWidth - 2 * GAP;
    const first = pageSizes[0];
    if (zoomMode === "fit") return Math.max(0.2, avail / first.w);
    if (zoomMode === "page") return Math.max(0.2, Math.min(avail / first.w, (scroller.clientHeight - 2 * GAP) / first.h));
    return parseFloat(zoomMode) || 1;
  }

  function syncZoomSelect() {
    const opt = [...zoomSel.options].find((o) => o.value === zoomMode);
    if (opt) zoomSel.value = zoomMode;
    else { const c = zoomSel.querySelector('[value="custom"]'); c.textContent = Math.round(scale * 100) + "%"; zoomSel.value = "custom"; }
  }

  // Build page placeholders (sized), observe them, and render lazily.
  function buildPages(doc) {
    const el = document.createElement("div");
    el.className = "pdfjs-pages";
    pageSizes.forEach((sz, i) => {
      const pg = document.createElement("div");
      pg.className = "pdfjs-page";
      pg.dataset.page = i + 1;
      pg.style.width = Math.floor(sz.w * scale) + "px";
      pg.style.height = Math.floor(sz.h * scale) + "px";
      el.appendChild(pg);
    });
    return el;
  }

  async function renderPage(doc, pg, gen) {
    if (pg.dataset.rendered === String(scale) || gen !== renderGen) return;
    pg.dataset.rendered = String(scale);
    const page = await doc.getPage(+pg.dataset.page);
    if (gen !== renderGen) { delete pg.dataset.rendered; return; }
    const dpr = window.devicePixelRatio || 1;
    const vp = page.getViewport({ scale: scale * dpr });
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(vp.width);
    canvas.height = Math.floor(vp.height);
    canvas.style.width = pg.style.width;
    canvas.style.height = pg.style.height;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise.catch(() => {});
    if (gen !== renderGen) return;
    pg.querySelectorAll("canvas").forEach((c) => c.remove());
    pg.prepend(canvas);
  }

  function observe(doc, gen) {
    if (observer) observer.disconnect();
    observer = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) renderPage(doc, e.target, gen); });
    }, { root: scroller, rootMargin: "600px 0px" });
    pagesEl.querySelectorAll(".pdfjs-page").forEach((p) => observer.observe(p));
  }

  function pageTop(n) {   // offset of page n (1-based) inside pagesEl
    const el = pagesEl.querySelector(`.pdfjs-page[data-page="${n}"]`);
    return el ? el.offsetTop : 0;
  }

  function currentPage() {
    const mid = scroller.scrollTop + scroller.clientHeight / 3;
    let cur = 1;
    pagesEl.querySelectorAll(".pdfjs-page").forEach((p) => { if (p.offsetTop <= mid) cur = +p.dataset.page; });
    return cur;
  }

  // Position as (page, fraction within page) so it survives zoom and recompiles.
  function savePosition() {
    if (!pdf) return null;
    const page = currentPage();
    const el = pagesEl.querySelector(`.pdfjs-page[data-page="${page}"]`);
    const frac = el ? (scroller.scrollTop - el.offsetTop) / el.offsetHeight : 0;
    return { page, frac, left: scroller.scrollLeft };
  }
  function restorePosition(pos) {
    if (!pos) return;
    const el = pagesEl.querySelector(`.pdfjs-page[data-page="${Math.min(pos.page, pageSizes.length)}"]`);
    if (el) scroller.scrollTop = el.offsetTop + pos.frac * el.offsetHeight;
    scroller.scrollLeft = pos.left || 0;
  }

  async function load(url, opts = {}) {
    const seq = ++loadSeq;
    const pos = opts.keepScroll !== false ? savePosition() : null;
    loading = true;
    try { await loadInner(url, opts, seq, pos); } finally {
      if (seq === loadSeq) {
        loading = false;
        if (rerenderPending) { rerenderPending = false; rerender(); }
      }
    }
  }

  async function loadInner(url, opts, seq, pos) {
    let doc;
    try {
      doc = await pdfjsLib.getDocument({ url, isEvalSupported: false }).promise;
    } catch (e) {
      if (seq === loadSeq) OC.toast("Could not load PDF: " + e.message, true);
      return;
    }
    if (seq !== loadSeq) { doc.destroy(); return; }
    const sizes = [];
    for (let i = 1; i <= doc.numPages; i++) {
      const p = await doc.getPage(i);
      const v = p.getViewport({ scale: 1 });
      sizes.push({ w: v.width, h: v.height });
    }
    if (seq !== loadSeq) { doc.destroy(); return; }
    const old = pdf;
    pdf = doc;
    pageSizes = sizes;
    ui.hidden = false;
    scale = computeScale();
    syncZoomSelect();
    // Build the new page list off-screen, render the pages that will be visible,
    // then swap it in so the preview doesn't flash white on every recompile.
    const fresh = buildPages(doc);
    fresh.style.visibility = "hidden";
    scroller.appendChild(fresh);
    const prevEl = pagesEl;
    pagesEl = fresh;
    if (opts.page) {
      scroller.scrollTop = pageTop(opts.page);
    } else {
      restorePosition(pos);
    }
    const visible = [...fresh.querySelectorAll(".pdfjs-page")].filter((p) =>
      p.offsetTop < scroller.scrollTop + scroller.clientHeight + 200 && p.offsetTop + p.offsetHeight > scroller.scrollTop - 200);
    const gen = ++renderGen;
    await Promise.all(visible.map((p) => renderPage(doc, p, gen)));
    if (seq !== loadSeq) { fresh.remove(); pagesEl = prevEl; return; }
    prevEl.remove();
    fresh.style.visibility = "";
    if (opts.page) scroller.scrollTop = pageTop(opts.page); else restorePosition(pos);
    observe(doc, gen);
    ui.querySelector(".count").textContent = "/ " + doc.numPages;
    pageInput.max = doc.numPages;
    updatePageInput();
    if (opts.highlight) highlight(opts.highlight);
    if (old) old.destroy();
  }

  function rerender() {
    if (!pdf) return;
    if (loading) { rerenderPending = true; return; }
    const pos = savePosition();
    const newScale = computeScale();
    if (Math.abs(newScale - scale) < 0.001 && pagesEl.children.length) return;
    scale = newScale;
    syncZoomSelect();
    const gen = ++renderGen;
    pagesEl.querySelectorAll(".pdfjs-page").forEach((pg, i) => {
      pg.style.width = Math.floor(pageSizes[i].w * scale) + "px";
      pg.style.height = Math.floor(pageSizes[i].h * scale) + "px";
      pg.querySelectorAll("canvas").forEach((c) => { c.style.width = pg.style.width; c.style.height = pg.style.height; });
      delete pg.dataset.rendered;
    });
    restorePosition(pos);
    observe(pdf, gen);
  }

  function setZoom(mode) {
    zoomMode = String(mode);
    localStorage.setItem("oc-pdf-zoom", zoomMode);
    rerender();
  }

  function stepZoom(dir) {
    const steps = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4];
    const next = dir > 0 ? steps.find((s) => s > scale + 0.01) : [...steps].reverse().find((s) => s < scale - 0.01);
    if (next) setZoom(next);
  }

  function updatePageInput() { if (pdf) pageInput.value = currentPage(); }

  function highlight({ page, x, y, h, v, width, height }) {
    const pg = pagesEl.querySelector(`.pdfjs-page[data-page="${page}"]`);
    if (!pg) return;
    pg.querySelectorAll(".sync-mark").forEach((m) => m.remove());
    const mark = document.createElement("div");
    mark.className = "sync-mark";
    const top = ((v ?? y) - (height || 12)) * scale;
    mark.style.left = (h ?? x ?? 0) * scale + "px";
    mark.style.top = top + "px";
    mark.style.width = Math.max(40, (width || 300) * scale) + "px";
    mark.style.height = Math.max(14, ((height || 12) + 4) * scale) + "px";
    pg.appendChild(mark);
    scroller.scrollTop = pg.offsetTop + top - scroller.clientHeight / 3;
    setTimeout(() => mark.remove(), 2500);
  }

  // ------------------------------------------------------------- events
  ui.querySelector(".pdfjs-bar").addEventListener("click", (e) => {
    const act = e.target.dataset.act;
    if (act === "in") stepZoom(1);
    else if (act === "out") stepZoom(-1);
    else if (act === "prev") scroller.scrollTop = pageTop(Math.max(1, currentPage() - 1));
    else if (act === "next") scroller.scrollTop = pageTop(Math.min(pageSizes.length, currentPage() + 1));
    else if (act === "dark") {
      dark = !dark;
      localStorage.setItem("oc-pdf-dark", dark ? "1" : "0");
      ui.classList.toggle("dark-pdf", dark);
    }
  });
  zoomSel.onchange = () => { if (zoomSel.value !== "custom") setZoom(zoomSel.value); };
  pageInput.onchange = () => { scroller.scrollTop = pageTop(Math.max(1, Math.min(pageSizes.length, +pageInput.value || 1))); };
  scroller.addEventListener("scroll", OC.debounce(updatePageInput, 80));
  scroller.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    stepZoom(e.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  new ResizeObserver(OC.debounce(() => { if (zoomMode === "fit" || zoomMode === "page") rerender(); }, 150)).observe(scroller);

  // Reverse SyncTeX: double-click on the PDF.
  scroller.addEventListener("dblclick", async (e) => {
    const pg = e.target.closest(".pdfjs-page");
    if (!pg) return;
    const r = pg.getBoundingClientRect();
    const x = (e.clientX - r.left) / scale;
    const y = (e.clientY - r.top) / scale;
    try {
      const res = await OC.api("GET", OC.projectUrl(`/synctex/edit?page=${pg.dataset.page}&x=${x}&y=${y}`));
      if (res.path) OC.gotoLine(res.path, res.line);
      else OC.toast(res.reason || "Couldn't map that point to the source.");
    } catch (err) { OC.toast(err.message, true); }
  });

  // ------------------------------------------------------------- public
  OC.pdfViewer = {
    load,
    highlight,
    get loaded() { return !!pdf; },
    clear() { loadSeq++; renderGen++; loading = false; if (pdf) pdf.destroy(); pdf = null; pageSizes = []; pagesEl.innerHTML = ""; ui.hidden = true; },
  };

  // Forward search with highlight (replaces the default "→ PDF" behaviour).
  const syncBtn = document.querySelector("#syncToPdf");
  syncBtn.onclick = async () => {
    const tab = OC.activeTab();
    if (!tab || tab.binary) return;
    const line = OC.editor.getCursor().line + 1;
    try {
      const r = await OC.api("GET", OC.projectUrl(`/synctex?path=${OC.enc(tab.path)}&line=${line}`));
      if (!r.page) return OC.toast("Couldn't locate this line in the PDF (compile first).");
      OC.switchPreview("pdf");
      highlight(r);
    } catch (e) { OC.toast(e.message, true); }
  };

  OC.on("projectOpened", () => OC.pdfViewer.clear());
})();
