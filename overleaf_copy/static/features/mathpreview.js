/* Live equation preview: when the cursor is inside maths ($…$, \[…\], equation, align, …)
 * a rendered preview (KaTeX, offline) appears under the line. Your \newcommand macros are used.
 */
(() => {
  const OC = window.OC;
  if (!window.katex) return;
  const pop = OC.el('<div class="math-preview" hidden></div>');
  document.body.appendChild(pop);
  let macros = {};

  const ENV_MAP = {
    equation: null, "equation*": null, displaymath: null, math: null,
    align: "aligned", "align*": "aligned", flalign: "aligned", "flalign*": "aligned",
    alignat: "alignedat", "alignat*": "alignedat",
    gather: "gathered", "gather*": "gathered",
    multline: "gathered", "multline*": "gathered",
    eqnarray: "aligned", "eqnarray*": "aligned",
  };
  const REGION_RE = /\$\$([\s\S]+?)\$\$|\$((?:\\.|[^$\\])+?)\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|flalign\*?|alignat\*?|eqnarray\*?|displaymath|math)\}([\s\S]*?)\\end\{\5\}/g;

  /** Replace comments and escaped dollars with spaces so offsets stay the same. */
  function mask(text) {
    return text.replace(/\\\$/g, "  ").replace(/(^|[^\\])%[^\n]*/g, (m, p) => p + " ".repeat(m.length - p.length));
  }

  function collectMacros() {
    macros = {};
    for (const tab of OC.state.openTabs) {
      if (tab.binary) continue;
      const t = tab.doc.getValue();
      const re = /\\(?:re)?newcommand\*?\s*\{?\\([A-Za-z]+)\}?\s*(?:\[(\d)\])?\s*\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}/g;
      let m;
      while ((m = re.exec(t))) macros["\\" + m[1]] = m[3];
      const op = /\\DeclareMathOperator\*?\s*\{\\([A-Za-z]+)\}\s*\{([^}]*)\}/g;
      while ((m = op.exec(t))) macros["\\" + m[1]] = `\\operatorname{${m[2]}}`;
    }
  }

  function findRegion(cm) {
    const doc = cm.getDoc();
    const text = mask(doc.getValue());
    const idx = doc.indexFromPos(cm.getCursor());
    REGION_RE.lastIndex = 0;
    let m;
    while ((m = REGION_RE.exec(text))) {
      const start = m.index, end = m.index + m[0].length;
      if (start > idx) break;
      if (idx >= start && idx <= end) {
        const raw = doc.getValue().slice(start, end);
        if (m[1] !== undefined) return { tex: raw.slice(2, -2), display: true, end };
        if (m[2] !== undefined) return { tex: raw.slice(1, -1), display: false, end };
        if (m[3] !== undefined || m[4] !== undefined) return { tex: raw.slice(2, -2), display: m[3] !== undefined, end };
        const env = m[5];
        const inner = raw.slice(`\\begin{${env}}`.length, raw.length - `\\end{${env}}`.length);
        const wrap = ENV_MAP[env];
        let tex = inner;
        if (env.startsWith("alignat")) tex = `\\begin{alignedat}${inner}\\end{alignedat}`;   // keeps {n}
        else if (wrap) tex = `\\begin{${wrap}}${inner}\\end{${wrap}}`;
        return { tex, display: true, end };
      }
    }
    return null;
  }

  function clean(tex) {
    return tex.replace(/\\label\{[^}]*\}/g, "").replace(/\\(nonumber|notag)\b/g, "").replace(/\\intertext\{[^}]*\}/g, "");
  }

  const update = OC.debounce(() => {
    const cm = OC.editor;
    const tab = OC.activeTab();
    const enabled = !OC.settings || OC.settings.get().mathPreview;
    if (!cm || !tab || tab.binary || !/\.(tex|ltx)$/i.test(tab.path) || !enabled || !cm.hasFocus()) { pop.hidden = true; return; }
    const region = findRegion(cm);
    if (!region || !region.tex.trim()) { pop.hidden = true; return; }
    try {
      pop.innerHTML = katex.renderToString(clean(region.tex), { displayMode: region.display, throwOnError: false, macros: { ...macros }, strict: "ignore", trust: false, errorColor: "#c0392b" });
    } catch (e) {
      pop.textContent = e.message;
    }
    const endPos = cm.getDoc().posFromIndex(region.end);
    const cur = cm.getCursor();
    const at = cm.cursorCoords({ line: Math.max(cur.line, endPos.line), ch: 0 }, "window");
    const host = cm.getWrapperElement().getBoundingClientRect();
    pop.hidden = false;
    pop.style.left = host.left + 50 + "px";
    pop.style.maxWidth = host.width - 70 + "px";
    let top = at.bottom + 8;
    if (top + pop.offsetHeight > window.innerHeight - 10) top = cm.cursorCoords(cur, "window").top - pop.offsetHeight - 10;
    pop.style.top = top + "px";
  }, 120);

  let attached = false;
  OC.on("projectOpened", () => {
    if (attached || !OC.editor) return;
    attached = true;
    OC.editor.on("cursorActivity", update);
    OC.editor.on("blur", () => setTimeout(() => { if (!OC.editor.hasFocus()) pop.hidden = true; }, 150));
    OC.editor.on("scroll", () => (pop.hidden = true));
  });
  OC.on("tabActivated", () => { collectMacros(); pop.hidden = true; });
  OC.on("saved", collectMacros);
  OC.mathPreview = { findRegion, update };
})();
