/* Pre-submission checklist, arXiv export and the To-do panel. */
(() => {
  const OC = window.OC;

  // ------------------------------------------------------------------ To-do
  const todoBody = OC.el(`<div class="side-body"><div id="todoList" class="muted">Open the panel to scan…</div></div>`);
  OC.addPanel("todos", "☑", "To-do", todoBody);

  async function refreshTodos() {
    if (!OC.state.project) return;
    const r = await OC.api("GET", OC.projectUrl("/todos"));
    const list = todoBody.querySelector("#todoList");
    list.className = "";
    list.innerHTML = "";
    if (r.undefined.length) {
      list.appendChild(OC.el(`<div class="group-title">Undefined references (show as ??)</div>`));
      const main = OC.state.settings.main;
      r.undefined.forEach((u) => {
        const it = OC.el(`<div class="list-item">❓ <b>${OC.escapeHtml(u.key)}</b> <span class="sub">${u.kind}</span></div>`);
        // The log line number refers to whichever file contains it; search the project for the key.
        it.onclick = () => findAndGo(u.key, main);
        list.appendChild(it);
      });
    }
    const byFile = {};
    r.todos.forEach((t) => (byFile[t.path] = byFile[t.path] || []).push(t));
    Object.keys(byFile).forEach((path) => {
      list.appendChild(OC.el(`<div class="group-title">${OC.escapeHtml(path)}</div>`));
      byFile[path].forEach((t) => {
        const it = OC.el(`<div class="list-item"><span class="pill">${OC.escapeHtml(t.kind)}</span> ${OC.escapeHtml(t.text)}<div class="sub">line ${t.line}</div></div>`);
        it.onclick = () => OC.gotoLine(t.path, t.line);
        list.appendChild(it);
      });
    });
    if (!r.todos.length && !r.undefined.length) list.innerHTML = '<div class="muted">Nothing to do — no TODO/FIXME comments, \\todo notes or undefined references. 🎉</div>';
    const btn = document.querySelector('#sidebarTabs button[data-panel="todos"]');
    const n = r.todos.length + r.undefined.length;
    btn.textContent = n ? `☑${n}` : "☑";
  }

  async function findAndGo(key, fallback) {
    for (const f of OC.allFiles().filter((x) => x.name.endsWith(".tex"))) {
      try {
        const d = await OC.api("GET", OC.projectUrl(`/file?path=${OC.enc(f.path)}`));
        const idx = d.content.split("\n").findIndex((l) => l.includes(key));
        if (idx >= 0) return OC.gotoLine(f.path, idx + 1);
      } catch (_) {}
    }
    OC.openFile(fallback);
  }

  OC.on("panelShown", (id) => { if (id === "todos") refreshTodos().catch((e) => OC.toast(e.message, true)); });
  OC.on("compiled", () => refreshTodos().catch(() => {}));
  OC.on("projectOpened", () => refreshTodos().catch(() => {}));

  // ------------------------------------------------------- Submission check
  function checkDialog() {
    const saved = JSON.parse(localStorage.getItem("oc-check-" + OC.state.project) || "{}");
    const form = OC.el(`<div>
      <p>Compiles your paper and runs the checks venues and arXiv commonly enforce.</p>
      <div class="row">
        <div style="flex:1"><label class="field">Page limit (optional)</label><input name="limit" type="number" min="1" value="${saved.limit || ""}" placeholder="e.g. 8"></div>
        <div style="flex:2"><label class="field"><input type="checkbox" name="anon" ${saved.anon ? "checked" : ""}> Double-blind (check anonymity)</label>
          <input name="names" placeholder="Your names/affiliations to look for, comma-separated" value="${OC.escapeHtml(saved.names || "")}"></div>
      </div>
      <div class="results"></div></div>`);
    const q = (n) => form.querySelector(`[name=${n}]`);
    const m = OC.modal({
      title: "Submission check", body: form, wide: true,
      buttons: [
        { label: "Export for arXiv…", onClick: (close) => { close(); arxivDialog(); } },
        { label: "Run checks", primary: true, onClick: (close, btn) => run(btn) },
      ],
    });
    async function run(btn) {
      const opts = { limit: q("limit").value, anon: q("anon").checked, names: q("names").value };
      localStorage.setItem("oc-check-" + OC.state.project, JSON.stringify(opts));
      btn.disabled = true;
      const res = form.querySelector(".results");
      res.innerHTML = '<p class="muted">Compiling and checking…</p>';
      try {
        await OC.saveAll();
        const r = await OC.api("POST", OC.projectUrl("/check"), {
          page_limit: opts.limit ? +opts.limit : null, anonymous: opts.anon,
          names: opts.names.split(",").map((s) => s.trim()).filter(Boolean),
        });
        const icon = { pass: "✔", fail: "✘", warn: "⚠", info: "ℹ" };
        res.innerHTML = `<p><b>${r.summary.pass} passed</b> · ${r.summary.warn} warning(s) · ${r.summary.fail} problem(s)</p>`;
        r.checks.forEach((c) => {
          const row = OC.el(`<div class="check-row ${c.status}"><div class="icon">${icon[c.status]}</div>
            <div style="flex:1"><div><b>${OC.escapeHtml(c.title)}</b></div><div class="detail">${OC.escapeHtml(c.detail)}</div><div class="items"></div></div></div>`);
          c.items.forEach((it) => {
            const d = OC.el(`<div class="list-item" style="padding:3px 4px">${it.path ? `<code>${OC.escapeHtml(it.path)}${it.line ? ":" + it.line : ""}</code> ` : ""}${OC.escapeHtml(it.text)}</div>`);
            if (it.path && OC.findNode(it.path)) d.onclick = () => { m.close(); OC.gotoLine(it.path, it.line); };
            row.querySelector(".items").appendChild(d);
          });
          res.appendChild(row);
        });
        OC.compile();   // refresh the PDF and log panes (latexmk sees it is up to date)
      } catch (e) { res.innerHTML = `<p class="issue error">${OC.escapeHtml(e.message)}</p>`; }
      finally { btn.disabled = false; }
    }
  }

  // ------------------------------------------------------------ arXiv export
  async function arxivDialog() {
    await OC.saveAll();
    let preview;
    try { preview = await OC.api("GET", OC.projectUrl("/export/arxiv/preview")); }
    catch (e) { return OC.toast(e.message, true); }
    const rows = preview.files.map((f) => `<tr><td>${f.included ? "✔" : "—"}</td><td><code>${OC.escapeHtml(f.path)}</code></td><td class="muted">${OC.escapeHtml(f.why)}</td></tr>`).join("");
    const form = OC.el(`<div>
      <p>Builds a <code>.zip</code> ready to upload to arXiv:</p>
      <ul>
        <li>${preview.has_bbl ? "✔ Includes the compiled bibliography (<code>.bbl</code>) — arXiv doesn't run BibTeX." : "⚠ No <code>.bbl</code> found — compile first if you use BibTeX/Biber."}</li>
        <li>${preview.engine === "pdflatex" ? "pdfLaTeX (arXiv's default)" : `Adds a 00README.json telling arXiv to use ${preview.engine}`}</li>
      </ul>
      <label><input type="checkbox" name="strip" checked> Remove comments from .tex files (arXiv makes your source public)</label><br>
      <label><input type="checkbox" name="used" ${preview.recorder ? "checked" : "disabled"}> Only include files LaTeX actually used ${preview.recorder ? "" : "(compile first)"}</label>
      <table style="margin-top:10px"><tr><th></th><th>File</th><th></th></tr>${rows}</table></div>`);
    OC.modal({
      title: "Export for arXiv", body: form, wide: true,
      buttons: [{ label: "⤓ Download arXiv zip", primary: true, onClick: (close) => {
        const strip = form.querySelector("[name=strip]").checked ? 1 : 0;
        const used = form.querySelector("[name=used]").checked ? 1 : 0;
        window.location.href = OC.projectUrl(`/export/arxiv?strip_comments=${strip}&only_used=${used}`);
        close();
      } }],
    });
  }

  OC.addTool("Submission check…", checkDialog, "Submit");
  OC.addTool("Export for arXiv…", arxivDialog, "Submit");
  OC.addTool("Show to-do list", () => OC.showPanel("todos"), "Submit");
})();
