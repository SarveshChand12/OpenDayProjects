/* Version history panel + latexdiff "changes PDF" tool. */
(() => {
  const OC = window.OC;
  let snapshots = [];

  const body = OC.el(`<div class="side-body">
      <button class="btn primary" id="histSave" style="width:100%">💾 Save a named version…</button>
      <div class="hint muted" style="margin:6px 0">Versions are also saved automatically when you compile.</div>
      <div id="histList"></div>
    </div>`);
  OC.addPanel("history", "🕘", "History", body);

  const fmt = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const kindLabel = { manual: "saved", auto: "auto", "before-restore": "before restore", compile: "compile" };

  async function refresh() {
    if (!OC.state.project) return;
    const r = await OC.api("GET", OC.projectUrl("/history"));
    snapshots = r.snapshots;
    const list = body.querySelector("#histList");
    list.innerHTML = "";
    if (r.unsaved_changes) {
      list.appendChild(OC.el(`<div class="list-item" title="Compare the current files with the latest version">
        <b>Current (unsaved version)</b><div class="sub">Changes since the last version</div></div>`));
      list.lastChild.onclick = () => snapshots.length ? openCompare(snapshots[0].id, "current") : null;
    }
    if (!snapshots.length) list.appendChild(OC.el('<div class="muted">No versions yet.</div>'));
    snapshots.forEach((s, i) => {
      const c = s.changes;
      const item = OC.el(`<div class="list-item">
        <div>${s.label ? `<b>🏷 ${OC.escapeHtml(s.label)}</b><br>` : ""}<span>${fmt(s.time)}</span>
          <span class="pill">${kindLabel[s.kind] || s.kind}</span></div>
        <div class="sub">${c.added ? `+${c.added} added · ` : ""}${c.modified ? `${c.modified} changed · ` : ""}${c.removed ? `${c.removed} removed · ` : ""}${s.files} files</div>
        <div class="sub">${s.changed_files.map(OC.escapeHtml).join(", ")}</div>
      </div>`);
      item.onclick = () => openCompare(snapshots[i + 1] ? snapshots[i + 1].id : null, s.id, s);
      list.appendChild(item);
    });
  }

  body.querySelector("#histSave").onclick = async () => {
    const label = prompt("Name this version (e.g. 'Submitted to ICML', 'Before reviewer changes'):");
    if (label === null) return;
    await OC.saveAll();
    await OC.api("POST", OC.projectUrl("/history"), { label, kind: "manual" });
    OC.toast("Version saved");
    refresh();
  };

  function snapOptions(selected, includeCurrent) {
    let html = includeCurrent ? `<option value="current" ${selected === "current" ? "selected" : ""}>Current files</option>` : "";
    snapshots.forEach((s) => {
      html += `<option value="${s.id}" ${s.id === selected ? "selected" : ""}>${fmt(s.time)}${s.label ? " — " + OC.escapeHtml(s.label) : ""}</option>`;
    });
    return html;
  }

  // ---------------------------------------------------------- compare modal
  async function openCompare(fromId, toId, snap) {
    await OC.saveAll();
    const wrap = OC.el(`<div>
      <div class="row">
        <label>From <select class="from">${snapOptions(fromId, false)}<option value="empty" ${fromId ? "" : "selected"}>(nothing — first version)</option></select></label>
        <label>To <select class="to">${snapOptions(toId, true)}</select></label>
      </div>
      <div style="display:flex; gap:12px; min-height:50vh">
        <div class="files" style="width:260px; overflow:auto; border-right:1px solid var(--border)"></div>
        <div class="diff" style="flex:1; overflow:auto"></div>
      </div></div>`);
    let current = null;
    const m = OC.modal({
      title: snap ? `Version from ${fmt(snap.time)}${snap.label ? " — " + snap.label : ""}` : "Compare versions",
      body: wrap, wide: true,
      buttons: [
        { label: "Rename / label…", onClick: () => labelSnapshot(wrap.querySelector(".to").value) },
        { label: "Delete version", onClick: (close) => deleteSnapshot(wrap.querySelector(".to").value, close) },
        { label: "📄 Changes PDF (latexdiff)", onClick: () => runLatexdiff(wrap.querySelector(".from").value, wrap.querySelector(".to").value) },
        { label: "↩ Restore this file", onClick: (close) => restore(wrap.querySelector(".to").value, current, close) },
        { label: "↩ Restore whole project", primary: true, onClick: (close) => restore(wrap.querySelector(".to").value, null, close) },
      ],
    });

    async function load() {
      const from = wrap.querySelector(".from").value;
      const to = wrap.querySelector(".to").value;
      const r = await OC.api("GET", OC.projectUrl(`/history/diff?from=${from}&to=${to}`));
      const changed = r.files.filter((f) => f.status !== "same");
      const filesEl = wrap.querySelector(".files");
      filesEl.innerHTML = changed.length ? "" : '<div class="muted">No differences.</div>';
      const icon = { added: "🟢", removed: "🔴", modified: "🟡" };
      changed.forEach((f, i) => {
        const d = OC.el(`<div class="list-item"><span>${icon[f.status]}</span> ${OC.escapeHtml(f.path)}
          ${f.stats ? `<div class="sub">+${f.stats.plus} −${f.stats.minus}</div>` : ""}</div>`);
        d.onclick = () => { filesEl.querySelectorAll(".list-item").forEach((x) => (x.style.background = "")); d.style.background = "var(--selected)"; show(f); };
        filesEl.appendChild(d);
        if (i === 0) d.click();
      });
      if (!changed.length) wrap.querySelector(".diff").innerHTML = "";
    }
    function show(f) {
      current = f.path;
      const diffEl = wrap.querySelector(".diff");
      if (f.binary) { diffEl.innerHTML = `<p class="muted">${OC.escapeHtml(f.path)} is a binary file (${f.status}).</p>`; return; }
      const lines = (f.diff || []).map((l) => {
        const cls = l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : "";
        return `<div class="dl ${cls}">${OC.escapeHtml(l) || "&nbsp;"}</div>`;
      });
      diffEl.innerHTML = `<div class="diffview">${lines.join("")}</div>`;
    }
    wrap.querySelector(".from").onchange = load;
    wrap.querySelector(".to").onchange = load;
    load().catch((e) => OC.toast(e.message, true));
    return m;
  }

  async function labelSnapshot(id) {
    if (id === "current") return OC.toast("Pick a saved version in 'To' first.");
    const s = snapshots.find((x) => x.id === id);
    const label = prompt("Label for this version:", (s && s.label) || "");
    if (label === null) return;
    await OC.api("PATCH", OC.projectUrl(`/history/${id}`), { label });
    refresh();
  }

  async function deleteSnapshot(id, close) {
    if (id === "current") return;
    if (!confirm("Delete this version from history?")) return;
    await OC.api("DELETE", OC.projectUrl(`/history/${id}`));
    close(); refresh();
  }

  async function restore(id, path, close) {
    if (id === "current") return OC.toast("Pick an older version in 'To' to restore it.", true);
    const what = path ? `"${path}"` : "the WHOLE project";
    if (!confirm(`Restore ${what} to this version? Your current state is saved in history first, so you can undo this.`)) return;
    await OC.saveAll();
    try {
      await OC.api("POST", OC.projectUrl(`/history/${id}/restore`), path ? { path } : {});
      await OC.refreshTree();
      await OC.reloadOpenTabs();
      OC.toast("Restored");
      close(); refresh();
    } catch (e) { OC.toast(e.message, true); }
  }

  // ------------------------------------------------------------ latexdiff
  async function runLatexdiff(oldId, newId, style = "UNDERLINE") {
    if (!oldId || oldId === "empty") return OC.toast("Choose an older version to compare against.", true);
    OC.toast("Building changes PDF with latexdiff…");
    try {
      const r = await OC.api("POST", OC.projectUrl("/latexdiff"), { old: oldId, new: newId || "current", style });
      if (r.ok) {
        window.open(OC.projectUrl(`/latexdiff.pdf?t=${Date.now()}`), "_blank");
        OC.toast("Changes PDF ready — opened in a new tab");
      } else {
        OC.modal({ title: "latexdiff failed", body: `<pre>${OC.escapeHtml(r.log || "")}</pre>`, wide: true });
      }
    } catch (e) { OC.toast(e.message, true); }
  }

  function latexdiffDialog() {
    refresh().then(() => {
      if (!snapshots.length) return OC.toast("Save a version first (History panel), e.g. the one you submitted.", true);
      const labelled = snapshots.find((s) => s.label) || snapshots[0];
      const wrap = OC.el(`<div>
        <p>Produce a PDF that marks everything added (blue, underlined) and deleted (red, struck out)
        since an earlier version. Journals usually ask for one when you resubmit.</p>
        <label class="field">Old version (e.g. what you submitted)</label><select class="old">${snapOptions(labelled.id, false)}</select>
        <label class="field">New version</label><select class="new">${snapOptions("current", true)}</select>
        <label class="field">Markup style</label>
        <select class="style">
          <option value="UNDERLINE">Underline additions, strike deletions (default)</option>
          <option value="CFONT">Coloured font</option>
          <option value="CHANGEBAR">Change bars in the margin</option>
          <option value="CCHANGEBAR">Colour + change bars</option>
        </select>
        <div class="hint">Uses <code>latexdiff</code> from your TeX distribution.</div></div>`);
      OC.modal({
        title: "Changes PDF (latexdiff)", body: wrap,
        buttons: [{ label: "Build changes PDF", primary: true, onClick: (close, btn) => {
          btn.disabled = true;
          runLatexdiff(wrap.querySelector(".old").value, wrap.querySelector(".new").value, wrap.querySelector(".style").value)
            .finally(() => { btn.disabled = false; close(); });
        } }],
      });
    });
  }

  OC.addTool("Changes PDF since a version (latexdiff)…", latexdiffDialog, "Versions");
  OC.addTool("Save a named version…", () => body.querySelector("#histSave").click(), "Versions");
  OC.addTool("Show version history", () => OC.showPanel("history"), "Versions");

  OC.on("panelShown", (id) => { if (id === "history") refresh().catch((e) => OC.toast(e.message, true)); });
  OC.on("compiled", () => { if (!body.closest(".side-panel").hidden) setTimeout(refresh, 300); });
  // Periodic automatic version while editing (server skips it if nothing changed).
  setInterval(() => {
    if (OC.state.project) OC.api("POST", OC.projectUrl("/history"), { kind: "auto" }).catch(() => {});
  }, 5 * 60 * 1000);
})();
