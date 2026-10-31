/* Project-wide search & replace panel (Ctrl/Cmd+Shift+H). */
(() => {
  const OC = window.OC;
  const body = OC.el(`<div class="side-body">
      <input id="srchQ" placeholder="Search all files…">
      <div class="row" style="margin:4px 0; gap:4px">
        <label title="Match case"><input type="checkbox" id="srchCase"> Aa</label>
        <label title="Whole word"><input type="checkbox" id="srchWhole"> Word</label>
        <label title="Regular expression"><input type="checkbox" id="srchRegex"> .*</label>
      </div>
      <input id="srchR" placeholder="Replace with… (regex: \\1 = first group)">
      <button class="btn" id="srchReplaceAll" style="width:100%; margin-top:4px">Replace all</button>
      <div id="srchInfo" class="hint muted" style="margin:6px 0"></div>
      <div id="srchResults"></div>
    </div>`);
  OC.addPanel("search", "🔍", "Search", body);
  const $ = (s) => body.querySelector(s);

  const params = () => ({ q: $("#srchQ").value, case: $("#srchCase").checked ? 1 : 0, whole: $("#srchWhole").checked ? 1 : 0, regex: $("#srchRegex").checked ? 1 : 0 });

  async function search() {
    const p = params();
    const res = $("#srchResults");
    if (!p.q) { res.innerHTML = ""; $("#srchInfo").textContent = ""; return; }
    await OC.saveAll();
    try {
      const r = await OC.api("GET", OC.projectUrl("/search?" + new URLSearchParams(p)));
      $("#srchInfo").textContent = `${r.total} match(es)` + (r.truncated ? " (showing first 2000)" : "");
      res.innerHTML = "";
      let lastPath = null;
      r.results.forEach((m) => {
        if (m.path !== lastPath) { res.appendChild(OC.el(`<div class="group-title">${OC.escapeHtml(m.path)}</div>`)); lastPath = m.path; }
        const before = m.text.slice(Math.max(0, m.col - 40), m.col);
        const hit = m.text.slice(m.col, m.col + m.len);
        const after = m.text.slice(m.col + m.len, m.col + m.len + 60);
        const it = OC.el(`<div class="list-item"><span class="sub">${m.line}:</span> ${OC.escapeHtml(before)}<mark>${OC.escapeHtml(hit)}</mark>${OC.escapeHtml(after)}</div>`);
        it.onclick = async () => {
          await OC.gotoLine(m.path, m.line, m.col);
          OC.editor.setSelection({ line: m.line - 1, ch: m.col }, { line: m.line - 1, ch: m.col + m.len });
        };
        res.appendChild(it);
      });
    } catch (e) { $("#srchInfo").textContent = e.message; res.innerHTML = ""; }
  }
  const searchSoon = OC.debounce(search, 300);
  $("#srchQ").addEventListener("input", searchSoon);
  $("#srchQ").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  ["#srchCase", "#srchWhole", "#srchRegex"].forEach((s) => $(s).addEventListener("change", search));

  $("#srchReplaceAll").onclick = async () => {
    const p = params();
    if (!p.q) return;
    const info = $("#srchInfo").textContent;
    if (!confirm(`Replace ${info || "all matches"} of "${p.q}" with "${$("#srchR").value}" in every file? (A version is saved in History first, so you can undo this.)`)) return;
    await OC.saveAll();
    try {
      await OC.api("POST", OC.projectUrl("/history"), { label: `Before replacing "${p.q.slice(0, 40)}"`, kind: "manual" }).catch(() => {});
      const r = await OC.api("POST", OC.projectUrl("/replace"), { ...p, regex: !!p.regex, case: p.case, whole: p.whole, replacement: $("#srchR").value });
      OC.toast(`Replaced ${r.total} occurrence(s) in ${Object.keys(r.changed).length} file(s)`);
      await OC.reloadOpenTabs();
      search();
    } catch (e) { OC.toast(e.message, true); }
  };

  function open() {
    OC.showPanel("search");
    const sel = OC.editor && OC.editor.getSelection();
    if (sel && !sel.includes("\n")) { $("#srchQ").value = sel; search(); }
    $("#srchQ").focus();
  }
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "H" || e.key === "h") && OC.state.project) { e.preventDefault(); open(); }
  });
  OC.addTool("Search & replace in all files (Ctrl+Shift+H)", open, "Edit");
})();
