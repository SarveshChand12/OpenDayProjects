/* Editor settings: font, keymap (Vim/Emacs/Sublime), wrapping, spell-check language, etc.
 * Stored in the browser (localStorage) — this is a single-user app.
 */
(() => {
  const OC = window.OC;
  const DEFAULTS = {
    fontSize: 14,
    fontFamily: "",
    lineHeight: 1.5,
    lineWrapping: true,
    lineNumbers: true,
    keyMap: "default",
    autoCloseBrackets: true,
    spellcheck: true,
    spellLang: "en_US",
    grammarServer: "",          // "" = local LanguageTool (http://localhost:8081), "public", or a URL
    autoCompileDelay: 2500,
    mathPreview: true,
  };
  let settings = { ...DEFAULTS, ...JSON.parse(localStorage.getItem("oc-settings") || "{}") };

  const loadedKeymaps = new Set(["default"]);
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  async function ensureKeymap(name) {
    if (loadedKeymaps.has(name)) return;
    await loadScript(`/static/vendor/codemirror/${name}.js`);
    loadedKeymaps.add(name);
  }

  async function apply() {
    const cm = OC.editor;
    document.documentElement.style.setProperty("--editor-font-size", settings.fontSize + "px");
    document.documentElement.style.setProperty("--editor-line-height", settings.lineHeight);
    if (settings.fontFamily) document.documentElement.style.setProperty("--mono", settings.fontFamily);
    else document.documentElement.style.removeProperty("--mono");
    if (!cm) return;
    try { await ensureKeymap(settings.keyMap); } catch (_) { settings.keyMap = "default"; }
    cm.setOption("keyMap", settings.keyMap);
    cm.setOption("lineWrapping", settings.lineWrapping);
    cm.setOption("lineNumbers", settings.lineNumbers);
    cm.setOption("autoCloseBrackets", settings.autoCloseBrackets ? { pairs: "()[]{}$$", explode: "[]{}" } : false);
    cm.refresh();
  }

  function set(patch) {
    settings = { ...settings, ...patch };
    localStorage.setItem("oc-settings", JSON.stringify(settings));
    apply();
    OC.emit("settingsChanged", settings);
  }

  OC.settings = { get: () => ({ ...settings }), set, DEFAULTS };

  async function openDialog() {
    let langs = ["en_US"];
    try { langs = (await OC.api("GET", "/api/dictionaries")).languages; } catch (_) {}
    const s = settings;
    const wrap = OC.el(`<form>
      <div class="row">
        <div style="flex:1"><label class="field">Font size</label><input name="fontSize" type="number" min="9" max="32" value="${s.fontSize}"></div>
        <div style="flex:1"><label class="field">Line height</label><input name="lineHeight" type="number" step="0.1" min="1" max="2.5" value="${s.lineHeight}"></div>
      </div>
      <label class="field">Editor font (CSS font-family, blank = default)</label>
      <input name="fontFamily" placeholder='e.g. "Fira Code", monospace' value="${OC.escapeHtml(s.fontFamily)}">
      <label class="field">Key bindings</label>
      <select name="keyMap">
        <option value="default">Default</option><option value="vim">Vim</option>
        <option value="emacs">Emacs</option><option value="sublime">Sublime Text</option>
      </select>
      <div class="row" style="margin-top:10px">
        <label><input type="checkbox" name="lineWrapping" ${s.lineWrapping ? "checked" : ""}> Wrap long lines</label>
        <label><input type="checkbox" name="lineNumbers" ${s.lineNumbers ? "checked" : ""}> Line numbers</label>
        <label><input type="checkbox" name="autoCloseBrackets" ${s.autoCloseBrackets ? "checked" : ""}> Auto-close brackets</label>
        <label><input type="checkbox" name="mathPreview" ${s.mathPreview ? "checked" : ""}> Equation preview</label>
      </div>
      <label class="field">Spell checking</label>
      <div class="row">
        <label><input type="checkbox" name="spellcheck" ${s.spellcheck ? "checked" : ""}> Underline misspelled words</label>
        <select name="spellLang" style="width:auto">${langs.map((l) => `<option value="${l}">${l.replace("_", "-")}</option>`).join("")}</select>
      </div>
      <div class="hint">Add more languages by putting Hunspell <code>xx_YY.aff</code>/<code>.dic</code> files in
        <code>static/vendor/dictionaries/xx_YY/</code>.</div>
      <label class="field">Grammar checker (LanguageTool)</label>
      <select name="grammarMode">
        <option value="">Local server at http://localhost:8081 (private)</option>
        <option value="public">Public api.languagetool.org (sends text over the internet)</option>
        <option value="custom">Custom URL…</option>
      </select>
      <input name="grammarUrl" placeholder="https://my-languagetool.example:8081" style="margin-top:6px">
      <label class="field">Auto-compile delay after typing stops (ms)</label>
      <input name="autoCompileDelay" type="number" min="500" step="250" value="${s.autoCompileDelay}">
    </form>`);
    wrap.keyMap.value = s.keyMap;
    wrap.spellLang.value = s.spellLang;
    const mode = s.grammarServer === "" || s.grammarServer === "public" ? s.grammarServer : "custom";
    wrap.grammarMode.value = mode;
    wrap.grammarUrl.value = mode === "custom" ? s.grammarServer : "";
    wrap.grammarUrl.hidden = mode !== "custom";
    wrap.grammarMode.onchange = () => (wrap.grammarUrl.hidden = wrap.grammarMode.value !== "custom");
    OC.modal({
      title: "Editor settings", body: wrap,
      buttons: [
        { label: "Reset to defaults", onClick: (close) => { set({ ...DEFAULTS }); close(); } },
        { label: "Save", primary: true, onClick: (close) => {
          const f = wrap;
          set({
            fontSize: Math.max(9, Math.min(32, +f.fontSize.value || 14)),
            lineHeight: Math.max(1, Math.min(2.5, +f.lineHeight.value || 1.5)),
            fontFamily: f.fontFamily.value.trim(),
            keyMap: f.keyMap.value,
            lineWrapping: f.lineWrapping.checked,
            lineNumbers: f.lineNumbers.checked,
            autoCloseBrackets: f.autoCloseBrackets.checked,
            mathPreview: f.mathPreview.checked,
            spellcheck: f.spellcheck.checked,
            spellLang: f.spellLang.value,
            grammarServer: f.grammarMode.value === "custom" ? f.grammarUrl.value.trim() : f.grammarMode.value,
            autoCompileDelay: Math.max(500, +f.autoCompileDelay.value || 2500),
          });
          close();
          OC.toast("Settings saved");
        } },
      ],
    });
  }

  OC.addTool("Editor settings…", openDialog, "Settings");
  OC.on("projectOpened", apply);
  OC.on("init", apply);
})();
