/* Insert helpers:
 *  - Paste (or drag) an image into the editor -> saved to figures/ and a figure snippet is inserted.
 *  - Table generator: paste cells from Excel/Sheets/CSV -> LaTeX tabular. Pasting tab-separated
 *    data straight into the editor offers to convert it.
 */
(() => {
  const OC = window.OC;

  // ---------------------------------------------------------- shared helpers
  /** Add \usepackage{pkg} to the main file's preamble if it isn't there. */
  async function ensurePackage(pkg, opts = "") {
    const main = OC.state.settings.main;
    if (!main) return false;
    const tab = OC.state.openTabs.find((t) => t.path === main && !t.binary);
    const text = tab ? tab.doc.getValue() : (await OC.api("GET", OC.projectUrl(`/file?path=${OC.enc(main)}`))).content;
    const re = new RegExp(`\\\\(usepackage|RequirePackage)(\\[[^\\]]*\\])?\\{[^}]*\\b${pkg}\\b[^}]*\\}`);
    if (re.test(text)) return false;
    // The class may already provide it (e.g. IEEEtran doesn't load booktabs, but beamer loads graphicx).
    if (pkg === "graphicx" && /\\documentclass(\[[^\]]*\])?\{beamer\}/.test(text)) return false;
    const lines = text.split("\n");
    let at = lines.findIndex((l) => /^\s*\\usepackage/.test(l));
    if (at < 0) at = lines.findIndex((l) => /^\s*\\documentclass/.test(l));
    if (at < 0) return false;
    // insert after the last consecutive \usepackage line in the first block
    while (at + 1 < lines.length && /^\s*\\usepackage/.test(lines[at + 1])) at++;
    const insertLine = `\\usepackage${opts ? `[${opts}]` : ""}{${pkg}}`;
    if (tab) tab.doc.replaceRange(insertLine + "\n", { line: at + 1, ch: 0 });
    else {
      lines.splice(at + 1, 0, insertLine);
      await OC.api("PUT", OC.projectUrl("/file"), { path: main, content: lines.join("\n") });
    }
    OC.toast(`Added \\usepackage{${pkg}} to ${main}`);
    return true;
  }

  function relFromMain(path) {
    // \includegraphics paths are relative to the main file's folder.
    const mainDir = (OC.state.settings.main || "").split("/").slice(0, -1);
    const parts = path.split("/");
    let i = 0;
    while (i < mainDir.length && mainDir[i] === parts[i]) i++;
    return [...Array(mainDir.length - i).fill(".."), ...parts.slice(i)].join("/");
  }

  // --------------------------------------------------------- image pasting
  async function uploadImage(file, cm) {
    const ext = (file.type.split("/")[1] || "png").replace("jpeg", "jpg").replace("svg+xml", "svg");
    const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
    let name = file.name && !/^image\.\w+$/.test(file.name) ? file.name.replace(/[^A-Za-z0-9._-]/g, "_") : `pasted-${stamp}.${ext}`;
    // Never overwrite an existing figure: add -1, -2, ... if the name is taken.
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name, extn = dot > 0 ? name.slice(dot) : "";
    for (let n = 1; OC.findNode(`figures/${name}`); n++) name = `${stem}-${n}${extn}`;
    if (!OC.findNode("figures")) {
      await OC.api("POST", OC.projectUrl("/new"), { path: "figures", type: "folder" }).catch(() => {});
    }
    const fd = new FormData();
    fd.append("files", new File([file], name, { type: file.type }));
    fd.append("folder", "figures");
    const r = await OC.api("POST", OC.projectUrl("/upload"), fd, true);
    const saved = r.saved[0];
    if (ext === "svg") OC.toast("SVG saved — note LaTeX can't include SVG directly; export it as PDF/PNG or use the svg package.", true);
    const label = saved.split("/").pop().replace(/\.\w+$/, "").replace(/[^A-Za-z0-9]+/g, "-").toLowerCase();
    const indent = cm.getLine(cm.getCursor().line).match(/^\s*/)[0];
    const snippet = [
      "\\begin{figure}[ht]",
      "  \\centering",
      `  \\includegraphics[width=0.8\\linewidth]{${relFromMain(saved)}}`,
      "  \\caption{}",
      `  \\label{fig:${label}}`,
      "\\end{figure}",
    ].join("\n" + indent);
    let from = cm.getCursor();
    const lineText = cm.getLine(from.line);
    let prefix = "";
    if (lineText.trim() !== "") {
      // Insert after the current line rather than splitting it.
      from = { line: from.line, ch: lineText.length };
      prefix = "\n" + indent;
    }
    cm.replaceRange(prefix + snippet + "\n", from, from);
    cm.setCursor({ line: from.line + (prefix ? 1 : 0) + 3, ch: indent.length + "  \\caption{".length });
    cm.focus();
    await OC.refreshTree();
    await ensurePackage("graphicx");
    OC.toast(`Image saved as ${saved}`);
  }

  function attach(cm) {
    cm.on("paste", (cm2, e) => {
      const items = [...(e.clipboardData ? e.clipboardData.items : [])];
      const img = items.find((i) => i.kind === "file" && i.type.startsWith("image/"));
      if (img && isTexTab()) {
        e.preventDefault();
        uploadImage(img.getAsFile(), cm2).catch((err) => OC.toast(err.message, true));
        return;
      }
      const text = e.clipboardData && e.clipboardData.getData("text/plain");
      if (text && isTexTab() && looksLikeTable(text)) {
        // Let the paste happen, then offer to convert it.
        const start = cm2.getCursor("from");
        setTimeout(() => offerConvert(cm2, start, cm2.getCursor(), text), 0);
      }
    });
    cm.on("drop", (cm2, e) => {
      const files = [...(e.dataTransfer ? e.dataTransfer.files : [])].filter((f) => f.type.startsWith("image/") || /\.(pdf|eps)$/i.test(f.name));
      if (!files.length || !isTexTab()) return;
      e.preventDefault();
      const pos = cm2.coordsChar({ left: e.clientX, top: e.clientY });
      cm2.setCursor(pos);
      (async () => { for (const f of files) await uploadImage(f, cm2); })().catch((err) => OC.toast(err.message, true));
    });
  }
  const isTexTab = () => { const t = OC.activeTab(); return t && !t.binary && /\.tex$/i.test(t.path); };

  // ------------------------------------------------------- table generator
  function looksLikeTable(text) {
    const lines = text.trim().split(/\r?\n/);
    return lines.length >= 2 && lines.filter((l) => l.includes("\t")).length >= Math.min(2, lines.length);
  }

  function parseTable(text) {
    const lines = text.replace(/\r/g, "").split("\n").filter((l) => l.trim() !== "");
    if (!lines.length) return [];
    // Markdown table
    if (lines.every((l) => l.trim().startsWith("|"))) {
      return lines.filter((l) => !/^\s*\|?\s*:?-{2,}/.test(l))
        .map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    }
    const sample = lines.slice(0, 5).join("\n");
    const delim = sample.includes("\t") ? "\t" : (sample.split(";").length > sample.split(",").length ? ";" : ",");
    if (delim === "\t") return lines.map((l) => splitCsv(l, delim));
    return parseCsv(text.replace(/\r/g, ""), delim).filter((r) => r.some((c) => c !== ""));
  }
  /** RFC-4180-style CSV: quoted fields may contain the delimiter, "" and newlines. */
  function parseCsv(text, d) {
    const rows = []; let row = []; let cur = ""; let q = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) {
        if (ch === '"' && text[i + 1] === '"') { cur += '"'; i++; }
        else if (ch === '"') q = false;
        else cur += ch;
      } else if (ch === '"') q = true;
      else if (ch === d) { row.push(cur.trim()); cur = ""; }
      else if (ch === "\n") { row.push(cur.trim()); rows.push(row); row = []; cur = ""; }
      else cur += ch;
    }
    if (cur !== "" || row.length) { row.push(cur.trim()); rows.push(row); }
    return rows;
  }
  function splitCsv(line, d) {
    if (d === "\t") return line.split("\t").map((c) => c.trim());
    const out = []; let cur = ""; let q = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (q) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch; }
      else if (ch === '"') q = true;
      else if (ch === d) { out.push(cur.trim()); cur = ""; }
      else cur += ch;
    }
    out.push(cur.trim());
    return out;
  }
  const escapeTex = (s) => s.replace(/\\/g, "\u0000").replace(/([&%$#_{}])/g, "\\$1")
    .replace(/~/g, "\\textasciitilde{}").replace(/\^/g, "\\textasciicircum{}").replace(/\u0000/g, "\\textbackslash{}");
  const isNum = (s) => /^[-+−]?\(?[$€£]?\d[\d,]*(\.\d+)?%?\)?$/.test(s.trim());

  function toLatex(rows, o) {
    if (!rows.length) return "";
    const ncol = Math.max(...rows.map((r) => r.length));
    rows = rows.map((r) => [...r, ...Array(ncol - r.length).fill("")]);
    const cell = (c) => (o.escape ? escapeTex(c) : c);
    let align = "";
    for (let j = 0; j < ncol; j++) {
      if (o.align !== "auto") { align += o.align; continue; }
      const body = rows.slice(o.header ? 1 : 0).map((r) => r[j]).filter(Boolean);
      align += body.length && body.every(isNum) ? "r" : (j === 0 ? "l" : "c");
    }
    if (o.vlines) align = "|" + align.split("").join("|") + "|";
    const top = o.booktabs ? "\\toprule" : "\\hline";
    const mid = o.booktabs ? "\\midrule" : "\\hline";
    const bot = o.booktabs ? "\\bottomrule" : "\\hline";
    const ind = o.float ? "    " : "  ";
    const line = (r, bold) => ind + r.map((c) => (bold && c ? `\\textbf{${cell(c)}}` : cell(c))).join(" & ") + " \\\\";
    const out = [];
    const tabIndent = o.float ? "  " : "";
    out.push(`${tabIndent}\\begin{tabular}{${align}}`);
    out.push(ind + top);
    rows.forEach((r, i) => {
      out.push(line(r, o.header && i === 0 && o.boldHeader));
      if (o.header && i === 0) out.push(ind + mid);
      else if (!o.booktabs && o.hlines && i < rows.length - 1) out.push(ind + "\\hline");
    });
    out.push(ind + bot);
    out.push(`${tabIndent}\\end{tabular}`);
    if (!o.float) return out.join("\n");
    return ["\\begin{table}[ht]", "  \\centering", `  \\caption{${o.caption}}`, `  \\label{tab:${o.label}}`, ...out, "\\end{table}"].join("\n");
  }

  function uniqueLabel(caption) {
    const used = new Set((window.LatexHints && LatexHints.keys.labels) || []);
    const editorText = OC.editor ? OC.editor.getValue() : "";
    const base = (caption || "").toLowerCase().replace(/\\[a-z]+/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 30) || "table";
    let label = base;
    for (let n = 2; used.has("tab:" + label) || editorText.includes(`\\label{tab:${label}}`); n++) label = `${base}-${n}`;
    return label;
  }

  function tableDialog(initial = "", onInsert) {
    const form = OC.el(`<div>
      <label class="field">Paste cells from Excel / Google Sheets, CSV, or a Markdown table</label>
      <textarea name="data" rows="7" placeholder="Method\tAccuracy\tTime&#10;Baseline\t81.2\t12.4&#10;Ours\t89.7\t9.8"></textarea>
      <div class="row">
        <label><input type="checkbox" name="header" checked> First row is a header</label>
        <label><input type="checkbox" name="boldHeader"> Bold header</label>
        <label><input type="checkbox" name="booktabs" checked> booktabs rules</label>
        <label><input type="checkbox" name="float" checked> Wrap in table float</label>
        <label><input type="checkbox" name="escape" checked> Escape special characters</label>
        <label><input type="checkbox" name="vlines"> Vertical lines</label>
      </div>
      <div class="row">
        <label>Alignment <select name="align" style="width:auto"><option value="auto">Auto (numbers right)</option><option value="l">Left</option><option value="c">Centre</option><option value="r">Right</option></select></label>
        <input name="caption" placeholder="Caption" style="flex:2">
        <input name="label" placeholder="label (tab:…)" style="flex:1">
      </div>
      <div style="display:flex; gap:12px">
        <div style="flex:1"><label class="field">Preview</label><div class="tbl-preview"></div></div>
        <div style="flex:1"><label class="field">LaTeX</label><pre class="tbl-code"></pre></div>
      </div></div>`);
    const f = (n) => form.querySelector(`[name=${n}]`);
    f("data").value = initial;
    const opts = () => ({
      header: f("header").checked, boldHeader: f("boldHeader").checked, booktabs: f("booktabs").checked,
      float: f("float").checked, escape: f("escape").checked, vlines: f("vlines").checked, hlines: false,
      align: f("align").value, caption: f("caption").value, label: f("label").value || uniqueLabel(f("caption").value),
    });
    const update = () => {
      const rows = parseTable(f("data").value);
      form.querySelector(".tbl-code").textContent = toLatex(rows, opts());
      form.querySelector(".tbl-preview").innerHTML = rows.length
        ? `<table>${rows.map((r, i) => `<tr>${r.map((c) => (i === 0 && f("header").checked ? `<th>${OC.escapeHtml(c)}</th>` : `<td>${OC.escapeHtml(c)}</td>`)).join("")}</tr>`).join("")}</table>`
        : '<p class="muted">Nothing yet.</p>';
    };
    form.addEventListener("input", update);
    update();
    OC.modal({
      title: "Insert table", body: form, wide: true,
      buttons: [{ label: "Insert table", primary: true, onClick: async (close) => {
        const code = form.querySelector(".tbl-code").textContent;
        if (!code) return;
        const o = opts();
        if (onInsert) onInsert(code); else { OC.editor.replaceSelection(code + "\n"); OC.editor.focus(); }
        close();
        if (o.booktabs) await ensurePackage("booktabs").catch(() => {});
      } }],
    });
    setTimeout(() => f("data").focus(), 50);
  }

  function offerConvert(cm, from, to, text) {
    document.querySelectorAll(".paste-offer").forEach((x) => x.remove());
    const coords = cm.cursorCoords(to, "window");
    const bar = OC.el(`<div class="paste-offer">That looks like spreadsheet data. <button class="btn primary">Convert to LaTeX table</button> <button class="btn ghost">✕</button></div>`);
    bar.style.left = Math.min(coords.left, window.innerWidth - 380) + "px";
    bar.style.top = Math.max(8, Math.min(coords.bottom + 6, window.innerHeight - 60)) + "px";
    document.body.appendChild(bar);
    const mark = cm.markText(from, to);
    const close = () => { bar.remove(); };
    bar.querySelector(".primary").onclick = () => {
      close();
      tableDialog(text, (code) => {
        const r = mark.find();
        if (r) cm.replaceRange(code, r.from, r.to); else cm.replaceSelection(code);
        mark.clear();
      });
    };
    bar.querySelector(".ghost").onclick = () => { close(); mark.clear(); };
    setTimeout(() => { if (document.body.contains(bar)) { close(); mark.clear(); } }, 12000);
  }

  let attached = false;
  OC.on("projectOpened", () => { if (!attached && OC.editor) { attach(OC.editor); attached = true; } });
  OC.addTool("Insert table (from Excel/CSV)…", () => tableDialog(), "Edit");
  OC.addTool("Insert image…", () => {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "image/*,.pdf,.eps";
    inp.onchange = () => inp.files[0] && uploadImage(inp.files[0], OC.editor).catch((e) => OC.toast(e.message, true));
    inp.click();
  }, "Edit");
  OC.insert = { ensurePackage, parseTable, toLatex, tableDialog, uploadImage };
})();
