/* Grammar & style checking with LanguageTool.
 * LaTeX is sent as LanguageTool "annotated text": commands, maths and comments are
 * marked as markup so only your prose is checked, and offsets map 1:1 to the source.
 */
(() => {
  const OC = window.OC;
  let marks = [];
  let lastMatches = [];
  let checkedTab = null;

  // Commands whose argument is prose that should be checked.
  const TEXT_ARGS = /^\\(section|subsection|subsubsection|paragraph|subparagraph|chapter|part|caption|footnote|textbf|textit|emph|underline|textsc|textsf|textrm|textsl|title|thanks|item|frametitle|framesubtitle|mbox|text)\*?$/;
  // Commands that stand for a word/number inside a sentence.
  const INTERPRET = { ref: "1", eqref: "(1)", autoref: "Section 1", cref: "Section 1", Cref: "Section 1", pageref: "1",
    cite: "[1]", citep: "[1]", citet: "Smith et al.", citeauthor: "Smith", citeyear: "2020", footnotemark: "" };
  const MATH_ENVS = /^(equation|align|gather|multline|eqnarray|displaymath|math|tikzpicture|verbatim|lstlisting|minted|tabular|tabularx|figure|table|thebibliography)\*?$/;

  /** Convert LaTeX source into [{text}|{markup, interpretAs}] parts covering every character. */
  function annotate(src) {
    const parts = [];
    const text = (t) => { if (!t) return; const last = parts[parts.length - 1]; if (last && "text" in last) last.text += t; else parts.push({ text: t }); };
    const markup = (m, interp) => { if (!m) return; parts.push(interp !== undefined ? { markup: m, interpretAs: interp } : { markup: m }); };
    let i = 0;
    const n = src.length;
    const preambleEnd = src.indexOf("\\begin{document}");
    if (preambleEnd > 0) { markup(src.slice(0, preambleEnd)); i = preambleEnd; }
    const balanced = (start) => {  // index after matching } for { at start
      let depth = 0;
      for (let k = start; k < n; k++) {
        if (src[k] === "\\") { k++; continue; }
        if (src[k] === "{") depth++;
        else if (src[k] === "}") { depth--; if (depth === 0) return k + 1; }
      }
      return n;
    };
    while (i < n) {
      const c = src[i];
      if (c === "%") {
        const e = src.indexOf("\n", i); const end = e < 0 ? n : e;
        markup(src.slice(i, end)); i = end; continue;
      }
      if (c === "$") {
        const dbl = src[i + 1] === "$";
        const close = src.indexOf(dbl ? "$$" : "$", i + (dbl ? 2 : 1));
        const end = close < 0 ? n : close + (dbl ? 2 : 1);
        markup(src.slice(i, end), "x"); i = end; continue;
      }
      if (c === "\\") {
        if (src.startsWith("\\[", i) || src.startsWith("\\(", i)) {
          const closer = src[i + 1] === "[" ? "\\]" : "\\)";
          const close = src.indexOf(closer, i + 2); const end = close < 0 ? n : close + 2;
          markup(src.slice(i, end), "x"); i = end; continue;
        }
        const m = /^\\([A-Za-z@]+)\*?/.exec(src.slice(i, i + 60));
        if (!m) {  // \% \& \\ etc.
          const ch = src[i + 1] || "";
          markup(src.slice(i, i + 2), ch === "\\" ? "\n" : /[%&$#_{}]/.test(ch) ? ch : " ");
          i += 2; continue;
        }
        const name = m[1];
        let j = i + m[0].length;
        if (name === "begin") {
          const envEnd = balanced(j);
          const env = src.slice(j + 1, envEnd - 1);
          if (MATH_ENVS.test(env)) {
            const endTag = `\\end{${env}}`;
            const close = src.indexOf(endTag, envEnd);
            // For figures/tables still check the caption: handled by recursing on content isn't
            // worth the complexity — skip the whole float.
            const end = close < 0 ? n : close + endTag.length;
            markup(src.slice(i, end), /figure|table/.test(env) ? "\n\n" : "x"); i = end; continue;
          }
          // other environments: the \begin{..}[..] is markup, content is text
          let k = envEnd;
          while (src[k] === "[") { const cl = src.indexOf("]", k); k = cl < 0 ? n : cl + 1; }
          markup(src.slice(i, k), "\n\n"); i = k; continue;
        }
        if (TEXT_ARGS.test("\\" + name)) {
          // skip optional [..], keep the {...} contents as text
          while (src[j] === "[") { const cl = src.indexOf("]", j); j = cl < 0 ? n : cl + 1; }
          const isHeading = /section|chapter|part|paragraph|title|caption|frametitle/.test(name);
          if (src[j] === "{") {
            markup(src.slice(i, j + 1), isHeading ? "\n\n" : "");
            const close = balanced(j);
            const inner = src.slice(j + 1, close - 1);
            // recurse on the argument so nested commands are handled too
            annotate(inner).forEach((p) => ("text" in p ? text(p.text) : markup(p.markup, p.interpretAs)));
            markup(src.slice(close - 1, close), isHeading ? "\n\n" : "");
            i = close; continue;
          }
          markup(src.slice(i, j), name === "item" ? "\n• " : ""); i = j; continue;
        }
        // Any other command: swallow its [opt]/{arg} groups.
        while (j < n && (src[j] === "[" || src[j] === "{")) {
          if (src[j] === "[") { const cl = src.indexOf("]", j); j = cl < 0 ? n : cl + 1; }
          else j = balanced(j);
        }
        const interp = INTERPRET[name];
        markup(src.slice(i, j), interp !== undefined ? interp : (/^(par|newline|linebreak|newpage|clearpage|maketitle|end)$/.test(name) ? "\n\n" : ""));
        i = j; continue;
      }
      if (c === "~") { markup("~", " "); i++; continue; }
      if (c === "{" || c === "}") { markup(c, ""); i++; continue; }
      // plain text run
      let k = i;
      while (k < n && !"%$\\~{}".includes(src[k])) k++;
      text(src.slice(i, k)); i = k;
    }
    return parts;
  }

  function clearMarks() { marks.forEach((m) => m.clear()); marks = []; }

  const body = OC.el(`<div class="side-body">
      <button class="btn primary" id="gramCheck" style="width:100%">✓ Check grammar &amp; style</button>
      <label style="display:block;margin:6px 0"><input type="checkbox" id="gramPicky"> Picky mode (more style suggestions)</label>
      <div class="hint muted" id="gramServer"></div>
      <div id="gramResults"></div>
    </div>`);
  OC.addPanel("proofread", "✓", "Proofread", body);

  function serverLabel() {
    const s = OC.settings ? OC.settings.get().grammarServer : "";
    return s === "public" ? "Using the public LanguageTool service." : s ? `Using LanguageTool at ${s}.` : "Using your local LanguageTool server (http://localhost:8081). Change in Tools → Editor settings.";
  }

  async function check() {
    const tab = OC.activeTab();
    if (!tab || tab.binary) return OC.toast("Open a .tex file first.");
    const src = tab.doc.getValue();
    const annotation = annotate(src);
    const settings = OC.settings ? OC.settings.get() : {};
    const lang = (settings.spellLang || "en_US").replace("_", "-");
    const res = body.querySelector("#gramResults");
    res.innerHTML = '<div class="muted">Checking…</div>';
    OC.showPanel("proofread");
    try {
      const r = await OC.api("POST", "/api/grammar", { annotation, language: lang, server: settings.grammarServer || "", picky: body.querySelector("#gramPicky").checked });
      checkedTab = tab;
      lastMatches = r.matches;
      render(tab, src);
    } catch (e) {
      res.innerHTML = `<div class="issue error"><div class="msg">${OC.escapeHtml(e.message)}</div></div>`;
    }
  }

  function render(tab, src) {
    clearMarks();
    const res = body.querySelector("#gramResults");
    res.innerHTML = `<div class="group-title">${lastMatches.length} suggestion(s) in ${OC.escapeHtml(tab.path)}</div>`;
    lastMatches.forEach((m) => {
      const from = tab.doc.posFromIndex(m.offset);
      const to = tab.doc.posFromIndex(m.offset + m.length);
      const mark = tab.doc.markText(from, to, { className: "cm-grammar", title: m.message });
      marks.push(mark);
      const item = OC.el(`<div class="list-item">
          <div><b>${OC.escapeHtml(src.slice(m.offset, m.offset + m.length) || "…")}</b> <span class="sub">line ${from.line + 1} · ${OC.escapeHtml(m.category)}</span></div>
          <div>${OC.escapeHtml(m.message)}</div>
          <div class="repl"></div>
        </div>`);
      item.onclick = (e) => {
        if (e.target.tagName === "BUTTON") return;
        const pos = mark.find();
        if (!pos) return;
        OC.gotoLine(tab.path, pos.from.line + 1, pos.from.ch).then(() => OC.editor.setSelection(pos.from, pos.to));
      };
      m.replacements.forEach((rep) => {
        const b = OC.el(`<button class="btn" style="padding:2px 8px;margin:4px 4px 0 0">${OC.escapeHtml(rep) || "(delete)"}</button>`);
        b.onclick = () => {
          const pos = mark.find();
          if (!pos) return;
          tab.doc.replaceRange(rep, pos.from, pos.to);
          mark.clear(); item.remove();
        };
        item.querySelector(".repl").appendChild(b);
      });
      res.appendChild(item);
    });
    if (!lastMatches.length) res.appendChild(OC.el('<div class="muted">No issues found 🎉</div>'));
  }

  body.querySelector("#gramCheck").onclick = check;
  OC.addTool("Check grammar & style (LanguageTool)", check, "Proofreading");
  OC.on("panelShown", (id) => { if (id === "proofread") body.querySelector("#gramServer").textContent = serverLabel(); });
  OC.on("tabActivated", (tab) => { if (tab !== checkedTab) clearMarks(); });
  OC.grammar = { annotate };
})();
