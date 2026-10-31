/* Offline, LaTeX-aware spell checking (Typo.js + Hunspell dictionaries in a Web Worker).
 * Misspelled words get a red wavy underline; right-click one for suggestions,
 * "Add to dictionary" (stored in your browser) or "Ignore".
 */
(() => {
  const OC = window.OC;
  const worker = new Worker("/static/features/spell-worker.js");
  const bad = new Set();          // words known to be misspelled
  const good = new Set();         // words known to be fine
  const pending = new Set();      // queued for checking
  let personal = new Set(JSON.parse(localStorage.getItem("oc-dictionary") || "[]"));
  const ignored = new Set();
  let ready = false;
  let overlay = null;
  let reqId = 0;
  const suggestWaiters = {};

  // Commands whose {arguments} are identifiers, not prose.
  const SKIP_ARGS = /^\\(ref|eqref|autoref|cref|Cref|pageref|label|cite\w*|nocite|begin|end|usepackage|RequirePackage|documentclass|includegraphics|input|include|subfile|bibliography|bibliographystyle|addbibresource|url|href|graphicspath|newcommand|renewcommand|providecommand|newenvironment|DeclareMathOperator|setlength|addtolength|setcounter|hypersetup|definecolor|color|textcolor|pagestyle|thispagestyle|lstinputlisting|verb|newtheorem|theoremstyle|tikzset|pgfplotsset|usetikzlibrary|geometry|hspace|vspace|includepdf)\*?$/;
  const VERBATIM_ENVS = /^(verbatim|lstlisting|minted|equation|align|gather|multline|eqnarray|math|displaymath|tikzpicture|comment)\*?$/;

  function isTex() {
    const t = OC.activeTab();
    return t && !t.binary && /\.(tex|ltx)$/i.test(t.path);
  }

  function makeOverlay() {
    return {
      name: "spell",
      startState: () => ({ math: null, env: null }),
      copyState: (s) => ({ ...s }),
      token(stream, st) {
        // Inside a math/verbatim environment: skip until \end{env}
        if (st.env) {
          const m = stream.match(new RegExp("^.*?\\\\end\\{" + st.env.replace("*", "\\*") + "\\}"));
          if (m) { st.env = null; return null; }
          stream.skipToEnd(); return null;
        }
        if (st.math) {
          const close = st.math;
          while (!stream.eol()) {
            if (stream.match(close)) { st.math = null; return null; }
            if (stream.peek() === "\\") stream.next();
            stream.next();
          }
          return null;
        }
        if (stream.match("%")) { stream.skipToEnd(); return null; }
        if (stream.match("$$")) { st.math = "$$"; return null; }
        if (stream.match("$")) { st.math = "$"; return null; }
        if (stream.match("\\[")) { st.math = "\\]"; return null; }
        if (stream.match("\\(")) { st.math = "\\)"; return null; }
        const cmd = stream.match(/^\\[A-Za-z@]+\*?/);
        if (cmd) {
          const name = cmd[0];
          if (name === "\\begin") {
            const env = stream.match(/^\{([^}]*)\}/);
            if (env && VERBATIM_ENVS.test(env[1])) st.env = env[1];
            return null;
          }
          if (SKIP_ARGS.test(name)) {
            // consume [opt] and {arg} groups (one level of nesting is enough in practice)
            let guard = 0;
            while (guard++ < 4 && stream.match(/^\s*(\[[^\]]*\]|\{(?:[^{}]|\{[^{}]*\})*\})/)) { /* skip */ }
          }
          return null;
        }
        if (stream.match(/^\\./)) return null;               // escaped char like \%
        const w = stream.match(/^[A-Za-zÀ-ɏ]+(?:'[A-Za-z]+)?/);
        if (w) {
          const word = w[0];
          if (word.length < 2 || /^[A-Z]+s?$/.test(word) || personal.has(word) || personal.has(word.toLowerCase()) || ignored.has(word)) return null;
          if (stream.peek() && /[0-9_]/.test(stream.peek())) return null;   // identifiers like fig1
          if (bad.has(word)) return "spell-error";
          if (!good.has(word)) queue(word);
          return null;
        }
        stream.next();
        return null;
      },
    };
  }

  let modeGen = 0;
  CodeMirror.defineMode("stex-spell", (config) =>
    CodeMirror.overlayMode(CodeMirror.getMode(config, "stex"), makeOverlay(), true));

  const flush = OC.debounce(() => {
    if (!pending.size || !ready) return;
    const words = [...pending];
    pending.clear();
    worker.postMessage({ type: "check", id: ++reqId, words });
  }, 150);
  function queue(word) { if (!pending.has(word)) { pending.add(word); flush(); } }

  const refreshOverlay = OC.debounce(() => {
    const cm = OC.editor;
    if (!cm) return;
    // CodeMirror's addOverlay() can't keep state (needed for maths/environments),
    // so combine the spell overlay with the LaTeX mode via the overlayMode addon.
    const enabled = !(OC.settings && !OC.settings.get().spellcheck) && isTex() && ready;
    overlay = enabled;
    const scroll = cm.getScrollInfo();
    // A new spec object each time forces CodeMirror to re-highlight with the latest word lists.
    cm.setOption("mode", enabled ? { name: "stex-spell", gen: ++modeGen } : "stex");
    cm.scrollTo(scroll.left, scroll.top);
  }, 50);

  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "loaded") { ready = true; bad.clear(); good.clear(); refreshOverlay(); }
    else if (m.type === "error") OC.toast("Spell checker: " + m.message, true);
    else if (m.type === "checked") {
      const b = new Set(m.bad);
      let newBad = false;
      m.words.forEach((w) => { if (b.has(w)) { bad.add(w); newBad = true; } else good.add(w); });
      if (newBad) refreshOverlay();
    } else if (m.type === "suggestions") {
      const fn = suggestWaiters[m.id];
      delete suggestWaiters[m.id];
      if (fn) fn(m.suggestions);
    }
  };

  function loadLang() {
    const lang = (OC.settings && OC.settings.get().spellLang) || "en_US";
    ready = false;
    worker.postMessage({ type: "load", lang });
  }

  function suggest(word) {
    return new Promise((resolve) => {
      const id = ++reqId;
      suggestWaiters[id] = resolve;
      worker.postMessage({ type: "suggest", id, word });
      setTimeout(() => { if (suggestWaiters[id]) { delete suggestWaiters[id]; resolve([]); } }, 8000);
    });
  }

  function savePersonal() { localStorage.setItem("oc-dictionary", JSON.stringify([...personal].sort())); }

  // Right-click menu on misspelled words.
  function attachContextMenu(cm) {
    cm.getWrapperElement().addEventListener("contextmenu", async (e) => {
      if (!overlay) return;
      const pos = cm.coordsChar({ left: e.clientX, top: e.clientY });
      const word = cm.findWordAt(pos);
      const text = cm.getRange(word.anchor, word.head);
      if (!bad.has(text) || personal.has(text) || ignored.has(text)) return;
      e.preventDefault();
      document.querySelectorAll(".context-menu").forEach((m) => m.remove());
      const menu = document.createElement("div");
      menu.className = "context-menu";
      menu.style.left = e.clientX + "px"; menu.style.top = e.clientY + "px";
      menu.innerHTML = '<div class="muted">Finding suggestions…</div>';
      document.body.appendChild(menu);
      const close = () => menu.remove();
      setTimeout(() => document.addEventListener("click", close, { once: true }), 0);
      const sugg = await suggest(text);
      menu.innerHTML = "";
      const add = (label, fn, cls) => {
        const d = document.createElement("div"); d.textContent = label; if (cls) d.className = cls;
        d.onclick = () => { close(); fn(); }; menu.appendChild(d);
      };
      if (!sugg.length) add("(no suggestions)", () => {}, "muted");
      sugg.forEach((s) => add(s, () => cm.replaceRange(s, word.anchor, word.head)));
      add("➕ Add to dictionary", () => { personal.add(text); savePersonal(); refreshOverlay(); });
      add("Ignore this session", () => { ignored.add(text); refreshOverlay(); });
    });
  }

  function manageDictionary() {
    const ta = OC.el('<textarea rows="14" placeholder="One word per line"></textarea>');
    ta.value = [...personal].sort().join("\n");
    const wrap = document.createElement("div");
    wrap.appendChild(OC.el('<p class="hint">Words here are never marked as misspelled (e.g. names, technical terms).</p>'));
    wrap.appendChild(ta);
    OC.modal({
      title: "Personal dictionary", body: wrap,
      buttons: [{ label: "Save", primary: true, onClick: (close) => {
        personal = new Set(ta.value.split(/\s+/).map((w) => w.trim()).filter(Boolean));
        savePersonal(); refreshOverlay(); close();
      } }],
    });
  }

  OC.spell = { isMisspelled: (w) => bad.has(w), refresh: refreshOverlay, suggest };
  OC.addTool("Personal dictionary…", manageDictionary, "Proofreading");

  let attached = false;
  OC.on("projectOpened", () => {
    if (!attached && OC.editor) { attachContextMenu(OC.editor); attached = true; }
    if (!ready) loadLang(); else refreshOverlay();
  });
  OC.on("tabActivated", refreshOverlay);
  OC.on("settingsChanged", (s) => { loadLang(); refreshOverlay(); });
})();
