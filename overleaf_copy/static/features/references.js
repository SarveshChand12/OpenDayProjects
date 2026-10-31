/* References panel: browse/search your .bib entries and click to cite,
 * add references by DOI / arXiv ID (or pasted BibTeX), and lint the bibliography.
 */
(() => {
  const OC = window.OC;
  let entries = [];
  let bibFiles = [];

  const body = OC.el(`<div class="side-body">
      <div class="row" style="gap:4px; margin-top:0">
        <button class="btn primary" id="refAdd" style="flex:1">＋ Add reference</button>
        <button class="btn" id="refLint" title="Check bibliography">Check</button>
      </div>
      <input id="refFilter" placeholder="Search title, author, key, year…">
      <div class="hint muted" style="margin:4px 0">Click an entry to cite it at the cursor.</div>
      <div id="refList"></div>
    </div>`);
  OC.addPanel("refs", "📚", "References", body);
  const $ = (s) => body.querySelector(s);

  async function refresh() {
    if (!OC.state.project) return;
    const r = await OC.api("GET", OC.projectUrl("/bib"));
    entries = r.entries;
    bibFiles = r.bib_files;
    render();
  }

  function render() {
    const q = $("#refFilter").value.toLowerCase().trim();
    const list = $("#refList");
    list.innerHTML = "";
    const shown = entries.filter((e) => !q || [e.key, e.title, e.authors, e.year, e.venue].join(" ").toLowerCase().includes(q));
    if (!entries.length) list.innerHTML = '<div class="muted">No .bib entries yet. Use “Add reference”.</div>';
    shown.forEach((e) => {
      const it = OC.el(`<div class="list-item" title="${OC.escapeHtml(e.path)}:${e.line}">
          <div><b>${OC.escapeHtml(e.title || "(no title)")}</b></div>
          <div class="sub">${OC.escapeHtml(e.authors)}${e.year ? " · " + e.year : ""}${e.venue ? " · " + OC.escapeHtml(e.venue) : ""}</div>
          <div class="sub"><code>${OC.escapeHtml(e.key)}</code> ${e.cited ? `<span class="pill">cited ${e.cited}×</span>` : '<span class="pill">not cited</span>'}
            <a href="#" class="open" style="float:right">edit</a></div>
        </div>`);
      it.onclick = (ev) => {
        ev.preventDefault();
        if (ev.target.classList.contains("open")) return OC.gotoLine(e.path, e.line);
        insertCite(e.key);
      };
      list.appendChild(it);
    });
  }
  $("#refFilter").addEventListener("input", render);

  /** Insert \cite{key}, or add the key to a \cite{...} the cursor is already in. */
  function insertCite(key) {
    const cm = OC.editor;
    const tab = OC.activeTab();
    if (!cm || !tab || tab.binary || !tab.path.endsWith(".tex")) return OC.toast("Open a .tex file and place the cursor where the citation goes.");
    const cur = cm.getCursor();
    const line = cm.getLine(cur.line);
    const before = line.slice(0, cur.ch);
    const m = before.match(/\\[A-Za-z]*cite[A-Za-z]*\*?(?:\[[^\]]*\])*\{([^}]*)$/);
    if (m) {
      const close = line.indexOf("}", cur.ch);
      const inside = line.slice(cur.ch - m[1].length, close < 0 ? line.length : close);
      const have = inside.split(",").map((s) => s.trim());
      key = key.split(",").filter((k) => !have.includes(k)).join(",");
      if (!key) return OC.toast("Already cited here");
      const insertAt = { line: cur.line, ch: close < 0 ? line.length : close };
      cm.replaceRange((inside.trim() ? "," : "") + key, insertAt);
    } else {
      const cmd = (localStorage.getItem("oc-cite-cmd") || "cite");
      const needsTilde = /\w$/.test(before);
      cm.replaceSelection(`${needsTilde ? "~" : ""}\\${cmd}{${key}}`);
    }
    cm.focus();
    OC.toast(`Cited ${key}`);
  }

  // -------------------------------------------------------- add reference
  function addDialog() {
    const form = OC.el(`<div>
      <label class="field">DOI, arXiv ID or URL</label>
      <div class="row" style="margin:0"><input name="id" placeholder="10.1145/3065386   or   1706.03762   or   https://arxiv.org/abs/…" style="flex:1"><button class="btn" name="look">Look up</button></div>
      <div class="hint">Needs an internet connection. Offline? Paste BibTeX below instead (e.g. from Google Scholar).</div>
      <label class="field">BibTeX</label>
      <textarea name="bib" rows="10" placeholder="@article{key,&#10;  author = {…},&#10;  title  = {…},&#10;  year   = {…}&#10;}"></textarea>
      <label class="field">Add to</label>
      <select name="file">${(bibFiles.length ? bibFiles : ["references.bib"]).map((f) => `<option>${OC.escapeHtml(f)}</option>`).join("")}</select>
      <label style="display:block;margin-top:8px"><input type="checkbox" name="cite" checked> Also cite it at the cursor</label>
    </div>`);
    const f = (n) => form.querySelector(`[name=${n}]`);
    const lookup = async () => {
      const id = f("id").value.trim();
      if (!id) return;
      f("look").disabled = true; f("look").textContent = "Looking up…";
      try {
        const r = await OC.api("POST", OC.projectUrl("/bib/fetch"), { id });
        f("bib").value = (f("bib").value.trim() ? f("bib").value.trim() + "\n\n" : "") + r.bibtex;
      } catch (e) { OC.toast(e.message, true); }
      finally { f("look").disabled = false; f("look").textContent = "Look up"; }
    };
    f("look").onclick = lookup;
    f("id").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); lookup(); } });
    OC.modal({
      title: "Add reference", body: form, wide: true,
      buttons: [{ label: "Add to bibliography", primary: true, onClick: async (close) => {
        const bibtex = f("bib").value.trim();
        if (!bibtex) return OC.toast("Look up a DOI/arXiv ID or paste BibTeX first.", true);
        try {
          await OC.saveAll();
          const r = await OC.api("POST", OC.projectUrl("/bib/add"), { path: f("file").value, bibtex });
          close();
          OC.toast(`Added ${r.added.join(", ")} to ${r.path}`);
          await OC.refreshTree();
          await OC.reloadOpenTabs();
          await refresh();
          if (window.LatexHints) LatexHints.keys.cites = [...new Set([...LatexHints.keys.cites, ...r.added])];
          if (f("cite").checked) insertCite(r.added.join(","));
        } catch (e) { OC.toast(e.message, true); }
      } }],
    });
    setTimeout(() => f("id").focus(), 50);
  }

  // ----------------------------------------------------------------- lint
  async function lintDialog() {
    await OC.saveAll();
    let r;
    try { r = await OC.api("GET", OC.projectUrl("/bib/lint")); } catch (e) { return OC.toast(e.message, true); }
    const wrap = OC.el(`<div><p>${r.entries} entries · ${r.cited} distinct keys cited ·
      <b>${r.problems.filter((p) => p.level === "error").length}</b> error(s),
      ${r.problems.filter((p) => p.level === "warning").length} warning(s),
      ${r.problems.filter((p) => p.level === "info").length} note(s)</p><div class="items"></div></div>`);
    const icon = { error: "✘", warning: "⚠", info: "ℹ" };
    const cls = { error: "fail", warning: "warn", info: "info" };
    if (!r.problems.length) wrap.appendChild(OC.el('<p class="check-row pass"><span class="icon">✔</span> Bibliography looks good.</p>'));
    let m;
    r.problems.forEach((p) => {
      const row = OC.el(`<div class="check-row ${cls[p.level]}"><div class="icon">${icon[p.level]}</div>
        <div style="flex:1">${OC.escapeHtml(p.message)}<div class="detail">${OC.escapeHtml(p.path)}:${p.line}</div></div></div>`);
      row.style.cursor = "pointer";
      row.onclick = () => { m.close(); OC.gotoLine(p.path, p.line); };
      wrap.querySelector(".items").appendChild(row);
    });
    m = OC.modal({ title: "Bibliography check", body: wrap, wide: true });
  }

  $("#refAdd").onclick = addDialog;
  $("#refLint").onclick = lintDialog;
  OC.addTool("Add reference by DOI / arXiv…", addDialog, "References");
  OC.addTool("Check bibliography…", lintDialog, "References");
  OC.addTool("Browse references", () => OC.showPanel("refs"), "References");
  OC.on("panelShown", (id) => { if (id === "refs") refresh().catch((e) => OC.toast(e.message, true)); });
  OC.on("saved", (tab) => { if (tab.path.endsWith(".bib") && !body.closest(".side-panel").hidden) refresh(); });
  OC.on("projectOpened", () => { entries = []; bibFiles = []; });
})();
