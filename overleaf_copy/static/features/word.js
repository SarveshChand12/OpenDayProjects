/* Word import: turn .docx (and, with LibreOffice, .doc/.odt/.rtf) into LaTeX.
 *  - Dashboard: "Import Word document…" creates a new project.
 *  - Editor: Tools → "Import Word document into this project…" adds it as a section file.
 *  - Clicking a .docx in the file tree offers "Convert to LaTeX".
 */
(() => {
  const OC = window.OC;
  let status = null;
  async function getStatus() {
    if (!status) { try { status = await OC.api("GET", "/api/word/status"); } catch (_) { status = { pandoc: false, accepts: [".docx"] }; } }
    return status;
  }

  function engineField(st) {
    return `<label class="field">Converter</label>
      <select name="engine">
        <option value="builtin">Built-in — understands papers: abstract, keywords, [1]-style citations → \\cite, reference list</option>
        <option value="pandoc" ${st.pandoc ? "" : "disabled"}>Pandoc${st.pandoc ? ` (${OC.escapeHtml(st.pandoc_version || "installed")})` : " — not installed"} — general-purpose, very robust</option>
      </select>
      ${st.pandoc ? "" : '<div class="hint">Optional: install <a href="https://pandoc.org/installing.html" target="_blank" rel="noopener">Pandoc</a> or run <code>pip install pypandoc_binary</code>, then restart.</div>'}`;
  }

  function reportBody(r, extra = "") {
    const s = r.stats || {};
    const facts = [
      s.references ? `${s.references} references (citations linked)` : "",
      s.tables ? `${s.tables} table(s)` : "",
      s.images ? `${s.images} image(s)` : "",
    ].filter(Boolean).join(" · ");
    const warn = (r.warnings || []).map((w) => `<li>${OC.escapeHtml(w)}</li>`).join("");
    return `<p>✔ Converted with the <b>${r.engine === "pandoc" ? "Pandoc" : "built-in"}</b> converter${facts ? ": " + facts : ""}.</p>
      ${extra}
      ${warn ? `<p><b>Please check:</b></p><ul>${warn}</ul>` : ""}
      <p class="hint">Word and LaTeX lay things out differently. Review headings, tables, figure sizes and equations,
      then compile. The original document is kept in the project's <code>word/</code> folder.</p>`;
  }

  // ------------------------------------------------------- new project
  async function newProjectDialog(file) {
    const st = await getStatus();
    const form = OC.el(`<form>
      <label class="field">Word document</label>
      <input name="file" type="file" accept="${st.accepts.join(",")}">
      <div class="hint">Accepted: ${st.accepts.join(", ")}${st.libreoffice ? "" : " (install LibreOffice to also accept .doc, .odt and .rtf)"}</div>
      <label class="field">Project name</label>
      <input name="name" placeholder="Defaults to the file name">
      <label class="field">Document style</label>
      <select name="docclass">
        <option value="article">Article (general paper / journal)</option>
        <option value="ieee">IEEE conference (two-column)</option>
        <option value="report">Report / thesis (chapters)</option>
      </select>
      ${engineField(st)}
    </form>`);
    const f = (n) => form.querySelector(`[name=${n}]`);
    if (file) {
      const dt = new DataTransfer(); dt.items.add(file); f("file").files = dt.files;
    }
    f("file").onchange = () => {
      const fl = f("file").files[0];
      if (fl && !f("name").value) f("name").value = fl.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9 _.\-]/g, "_");
      if (fl && /ieee/i.test(fl.name)) f("docclass").value = "ieee";
    };
    if (file) f("file").onchange();
    OC.modal({
      title: "Import a Word document", body: form,
      buttons: [{ label: "Convert to LaTeX", primary: true, onClick: async (close, btn) => {
        const fl = f("file").files[0];
        if (!fl) return OC.toast("Choose a Word document first.", true);
        const fd = new FormData();
        fd.append("file", fl);
        fd.append("name", f("name").value.trim());
        fd.append("docclass", f("docclass").value);
        fd.append("engine", f("engine").value);
        btn.disabled = true; btn.textContent = "Converting…";
        try {
          const r = await OC.api("POST", "/api/import/word", fd, true);
          close();
          await OC.openProject(r.name);
          OC.modal({ title: "Word document imported", body: reportBody(r),
            buttons: [{ label: "Compile now", primary: true, onClick: (c) => { c(); OC.compile(); } }] });
        } catch (e) { OC.toast(e.message, true); btn.disabled = false; btn.textContent = "Convert to LaTeX"; }
      } }],
    });
  }

  // ------------------------------------------------ into this project
  function inputSuggestion(r) {
    return `<p>Created <code>${OC.escapeHtml(r.path)}</code>. To include it in your paper, add this line to your main file:</p>
      <pre>\\input{${OC.escapeHtml(r.input)}}</pre>`;
  }

  async function afterImport(r) {
    await OC.refreshTree();
    await OC.openFile(r.path);
    OC.modal({
      title: "Word document converted", body: reportBody(r, inputSuggestion(r)),
      buttons: [{ label: "Add \\input to main file", primary: true, onClick: async (close) => {
        const main = OC.state.settings.main;
        if (!main) return OC.toast("No main file set.", true);
        await OC.openFile(main);
        const cm = OC.editor;
        const text = cm.getValue();
        if (text.includes(`\\input{${r.input}}`)) { close(); return OC.toast("Already included."); }
        const endDoc = text.lastIndexOf("\\end{document}");
        const bib = text.search(/\\(bibliographystyle|bibliography|printbibliography|begin\{thebibliography\})/);
        const at = cm.posFromIndex(bib > 0 && bib < endDoc ? bib : endDoc >= 0 ? endDoc : text.length);
        cm.replaceRange(`\\input{${r.input}}\n\n`, { line: at.line, ch: 0 });
        cm.setCursor({ line: at.line, ch: 0 });
        close();
        // Load any packages the converted text uses that the main file doesn't load yet.
        const added = [];
        for (const [pkg, opts] of r.packages || []) {
          if (OC.insert && await OC.insert.ensurePackage(pkg, opts).catch(() => false)) added.push(pkg);
        }
        OC.toast(`Added \\input{${r.input}} to ${main}` + (added.length ? ` and loaded ${added.join(", ")}` : ""));
      } }],
    });
  }

  async function intoProjectDialog() {
    if (!OC.state.project) return;
    const st = await getStatus();
    const form = OC.el(`<form>
      <p>Converts the document to a LaTeX file in this project (e.g. a chapter or section) and copies its images to <code>figures/</code>.</p>
      <label class="field">Word document</label>
      <input name="file" type="file" accept="${st.accepts.join(",")}">
      <label class="field">Save as</label>
      <input name="target" placeholder="sections/<file name>.tex">
      ${engineField(st)}
    </form>`);
    const f = (n) => form.querySelector(`[name=${n}]`);
    f("file").onchange = () => {
      const fl = f("file").files[0];
      if (fl && !f("target").value) f("target").value = "sections/" + fl.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "-").toLowerCase() + ".tex";
    };
    OC.modal({
      title: "Import Word document into this project", body: form,
      buttons: [{ label: "Convert", primary: true, onClick: async (close, btn) => {
        const fl = f("file").files[0];
        if (!fl) return OC.toast("Choose a Word document first.", true);
        const fd = new FormData();
        fd.append("file", fl);
        fd.append("target", f("target").value.trim());
        fd.append("engine", f("engine").value);
        btn.disabled = true; btn.textContent = "Converting…";
        try {
          const r = await OC.api("POST", OC.projectUrl("/import/word"), fd, true);
          close();
          await afterImport(r);
        } catch (e) { OC.toast(e.message, true); btn.disabled = false; btn.textContent = "Convert"; }
      } }],
    });
  }

  // ---------------------------------------- .docx clicked in the file tree
  OC.on("tabActivated", (tab) => {
    if (!tab || !tab.binary || !/\.(docx|doc|odt|rtf)$/i.test(tab.path)) return;
    const host = document.querySelector("#binaryPreview");
    host.innerHTML = "";
    const box = OC.el(`<div style="text-align:center">
        <p style="font-size:40px;margin:0">📝</p>
        <p><b>${OC.escapeHtml(tab.path)}</b> is a Word document.</p>
        <p><button class="btn primary">Convert to LaTeX</button></p>
        <p class="hint">Creates <code>${OC.escapeHtml(tab.path.replace(/\.[^.]+$/, ".tex"))}</code> next to it, with images copied to <code>figures/</code>.</p>
      </div>`);
    box.querySelector("button").onclick = async (e) => {
      const btn = e.target;
      btn.disabled = true; btn.textContent = "Converting…";
      try {
        const r = await OC.api("POST", OC.projectUrl("/convert-word"), { path: tab.path, engine: "builtin" });
        await afterImport(r);
      } catch (err) { OC.toast(err.message, true); }
      finally { btn.disabled = false; btn.textContent = "Convert to LaTeX"; }
    };
    host.appendChild(box);
  });

  // ------------------------------------------------------- dashboard
  OC.on("init", () => {
    const zipLabel = document.querySelector('label[for="importZip"]');
    if (!zipLabel || document.querySelector("#importWordBtn")) return;
    const btn = OC.el('<button id="importWordBtn" class="btn" type="button">📝 Import Word document (.docx)…</button>');
    btn.onclick = () => newProjectDialog();
    zipLabel.after(btn);
    // Dropping a .docx anywhere on the dashboard starts an import too.
    const dash = document.querySelector("#dashboard");
    dash.addEventListener("dragover", (e) => e.preventDefault());
    dash.addEventListener("drop", (e) => {
      const fl = [...(e.dataTransfer ? e.dataTransfer.files : [])].find((x) => /\.(docx|doc|odt|rtf)$/i.test(x.name));
      if (!fl) return;
      e.preventDefault();
      newProjectDialog(fl);
    });
  });

  OC.addTool("Import Word document into this project…", intoProjectDialog, "Edit");
  OC.word = { newProjectDialog, intoProjectDialog };
})();
