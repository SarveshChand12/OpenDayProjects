/* Notes to self, attached to lines. Click the gutter (left of the line numbers) or
 * press Ctrl/Cmd+Alt+M to add one. Notes are stored outside your .tex files, so they
 * never reach the PDF. They follow their line as you edit.
 */
(() => {
  const OC = window.OC;
  let notes = [];
  const bookmarks = new Map();   // note id -> CodeMirror bookmark (for open tabs)
  let showResolved = false;

  const body = OC.el(`<div class="side-body">
      <button class="btn primary" id="noteAdd" style="width:100%">＋ Note on current line</button>
      <label style="display:block;margin:6px 0"><input type="checkbox" id="noteShowResolved"> Show resolved</label>
      <div id="noteList"></div>
    </div>`);
  OC.addPanel("notes", "💬", "Notes", body);
  const $ = (s) => body.querySelector(s);

  async function load() {
    if (!OC.state.project) return;
    notes = (await OC.api("GET", OC.projectUrl("/notes"))).notes;
    render();
    decorate();
  }

  function render() {
    const list = $("#noteList");
    list.innerHTML = "";
    const shown = notes.filter((n) => showResolved || !n.resolved).sort((a, b) => a.path.localeCompare(b.path) || a.line - b.line);
    if (!shown.length) list.innerHTML = '<div class="muted">No notes. Click in the gutter left of a line number to add one.</div>';
    let last = null;
    shown.forEach((n) => {
      if (n.path !== last) { list.appendChild(OC.el(`<div class="group-title">${OC.escapeHtml(n.path)}</div>`)); last = n.path; }
      const it = OC.el(`<div class="list-item ${n.resolved ? "resolved" : ""}">
          <div class="note-text"></div>
          <div class="sub">line ${n.line} · ${new Date(n.updated * 1000).toLocaleDateString()}
            <span style="float:right"><a href="#" data-a="edit">edit</a> · <a href="#" data-a="res">${n.resolved ? "reopen" : "resolve"}</a> · <a href="#" data-a="del">delete</a></span></div>
        </div>`);
      it.querySelector(".note-text").textContent = n.text;
      it.onclick = async (e) => {
        e.preventDefault();
        const a = e.target.dataset.a;
        if (a === "edit") return edit(n);
        if (a === "res") { await OC.api("PATCH", OC.projectUrl(`/notes/${n.id}`), { resolved: !n.resolved }); return load(); }
        if (a === "del") { if (confirm("Delete this note?")) { await OC.api("DELETE", OC.projectUrl(`/notes/${n.id}`)); load(); } return; }
        if (OC.findNode(n.path)) OC.gotoLine(n.path, currentLine(n));
        else OC.toast("That file no longer exists.");
      };
      list.appendChild(it);
    });
    const btn = document.querySelector('#sidebarTabs button[data-panel="notes"]');
    const open = notes.filter((n) => !n.resolved).length;
    btn.textContent = open ? `💬${open}` : "💬";
  }

  function currentLine(n) {
    const bm = bookmarks.get(n.id);
    const pos = bm && bm.find();
    return pos ? pos.line + 1 : n.line;
  }

  // Put markers in the gutter for the active tab's notes; re-anchor if lines moved.
  function decorate() {
    const cm = OC.editor;
    const tab = OC.activeTab();
    if (!cm) return;
    cm.clearGutter("note-gutter");
    if (!tab || tab.binary) return;
    const doc = tab.doc;
    notes.filter((n) => n.path === tab.path && !n.resolved).forEach((n) => {
      let bm = bookmarks.get(n.id);
      if (!bm || bm.doc !== doc || !bm.find()) {
        // Re-anchor: if the stored line's text changed, look for the anchor text nearby.
        let line = Math.min(n.line - 1, doc.lineCount() - 1);
        if (n.anchor && doc.getLine(line) !== n.anchor) {
          for (let d = 1; d < 400; d++) {
            if (line - d >= 0 && doc.getLine(line - d) === n.anchor) { line -= d; break; }
            if (line + d < doc.lineCount() && doc.getLine(line + d) === n.anchor) { line += d; break; }
          }
        }
        bm = doc.setBookmark({ line, ch: 0 });
        bm.doc = doc;
        bookmarks.set(n.id, bm);
      }
      const pos = bm.find();
      if (!pos) return;
      const marker = document.createElement("div");
      marker.className = "note-marker";
      marker.textContent = "💬";
      marker.title = n.text;
      cm.setGutterMarker(pos.line, "note-gutter", marker);
    });
  }

  // After a save, persist moved line numbers so notes stay on their line.
  async function syncPositions(tab) {
    const updates = [];
    notes.filter((n) => n.path === tab.path).forEach((n) => {
      const bm = bookmarks.get(n.id);
      const pos = bm && bm.find();
      if (!pos) return;
      const anchor = tab.doc.getLine(pos.line) || "";
      if (pos.line + 1 !== n.line || anchor !== n.anchor) {
        n.line = pos.line + 1; n.anchor = anchor;
        updates.push({ id: n.id, line: n.line, anchor });
      }
    });
    if (updates.length) {
      await OC.api("POST", OC.projectUrl("/notes/positions"), { updates }).catch(() => {});
      render();
    }
  }

  function noteEditor(title, initial, onSave) {
    const ta = OC.el('<textarea rows="5" placeholder="e.g. Rephrase — reviewer 2 found this unclear. Add citation for the claim."></textarea>');
    ta.value = initial || "";
    const wrap = document.createElement("div");
    wrap.appendChild(ta);
    wrap.appendChild(OC.el('<div class="hint">Notes are private: they are not part of your .tex source or the PDF.</div>'));
    OC.modal({ title, body: wrap, buttons: [{ label: "Save note", primary: true, onClick: async (close) => {
      if (!ta.value.trim()) return;
      await onSave(ta.value.trim()); close();
    } }] });
    setTimeout(() => ta.focus(), 50);
  }

  function addAt(line) {
    const tab = OC.activeTab();
    if (!tab || tab.binary) return OC.toast("Open a text file first.");
    const existing = notes.find((n) => n.path === tab.path && !n.resolved && currentLine(n) === line + 1);
    if (existing) return edit(existing);
    noteEditor(`Note on ${tab.path}:${line + 1}`, "", async (text) => {
      await OC.api("POST", OC.projectUrl("/notes"), { path: tab.path, line: line + 1, anchor: tab.doc.getLine(line), text });
      await load();
      OC.showPanel("notes");
    });
  }

  function edit(n) {
    noteEditor(`Note on ${n.path}:${currentLine(n)}`, n.text, async (text) => {
      await OC.api("PATCH", OC.projectUrl(`/notes/${n.id}`), { text });
      load();
    });
  }

  $("#noteAdd").onclick = () => OC.editor && addAt(OC.editor.getCursor().line);
  $("#noteShowResolved").onchange = (e) => { showResolved = e.target.checked; render(); };

  let attached = false;
  OC.on("projectOpened", () => {
    bookmarks.clear();
    const cm = OC.editor;
    if (!attached && cm) {
      attached = true;
      const gutters = cm.getOption("gutters");
      if (!gutters.includes("note-gutter")) cm.setOption("gutters", ["note-gutter", ...gutters]);
      cm.on("gutterClick", (cm2, line, gutter) => { if (gutter === "note-gutter") addAt(line); });
      cm.addKeyMap({ "Ctrl-Alt-M": (c) => addAt(c.getCursor().line), "Cmd-Alt-M": (c) => addAt(c.getCursor().line) });
    }
    load().catch(() => {});
  });
  OC.on("tabActivated", decorate);
  OC.on("saved", (tab) => syncPositions(tab));
  OC.on("treeRefreshed", () => { if (OC.state.project) load().catch(() => {}); });
  OC.addTool("Add note to current line (Ctrl+Alt+M)", () => OC.editor && addAt(OC.editor.getCursor().line), "Writing");
  OC.addTool("Show notes", () => OC.showPanel("notes"), "Writing");
})();
