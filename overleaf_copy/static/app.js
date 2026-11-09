/* Overleaf Copy — frontend (vanilla JS + CodeMirror 5). */
(() => {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (sel) => document.querySelector(sel);
  const enc = encodeURIComponent;
  const state = {
    project: null,          // project name
    tree: [],
    settings: {},
    openTabs: [],           // [{path, doc, dirty, saving, mtime}]
    active: null,           // path of active tab
    issues: [],
    collapsed: new Set(),
    compiling: false,
    pendingCompile: false,
    pdfPage: null,
  };

  // ------------------------------------------------------------ event hub
  // Feature modules subscribe with OC.on("compiled", fn) etc.
  const listeners = {};
  function on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); }
  function emit(evt, ...args) {
    (listeners[evt] || []).forEach((fn) => { try { fn(...args); } catch (e) { console.error(evt, e); } });
  }

  async function api(method, url, body, isForm) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    }
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }

  let toastTimer;
  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (isError ? " error" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), isError ? 6000 : 2500);
  }

  function debounce(fn, ms) {
    let t;
    const d = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    d.cancel = () => clearTimeout(t);
    return d;
  }

  function fmtDate(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  const fileIcon = (name) => {
    const ext = name.split(".").pop().toLowerCase();
    if (ext === "tex") return "📝";
    if (ext === "bib") return "📚";
    if (["png", "jpg", "jpeg", "gif", "svg", "eps"].includes(ext)) return "🖼️";
    if (ext === "pdf") return "📕";
    if (["cls", "sty", "bst"].includes(ext)) return "⚙️";
    return "📄";
  };

  // ------------------------------------------------------------------ theme
  function applyTheme(dark) {
    document.body.classList.toggle("dark", dark);
    localStorage.setItem("oc-dark", dark ? "1" : "0");
    if (editor) editor.setOption("theme", dark ? "material-darker" : "default");
  }
  $("#themeToggle").onclick = $("#themeToggleDash").onclick =
    () => applyTheme(!document.body.classList.contains("dark"));

  // ============================================================ DASHBOARD
  let templates = [];

  async function loadDashboard() {
    state.project = null;
    $("#dashboard").hidden = false;
    $("#editorView").hidden = true;
    document.title = "Overleaf Copy — Local LaTeX Editor";
    history.replaceState(null, "", "/");

    const status = await api("GET", "/api/status");
    templates = status.templates;
    const sel = $("#newProjectTemplate");
    sel.innerHTML = "";
    templates.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.id; o.textContent = t.id;
      if (t.id === "research-paper") o.selected = true;
      sel.appendChild(o);
    });
    updateTemplateDesc();

    const tools = status.tools;
    const engines = ["pdflatex", "xelatex", "lualatex"].filter((e) => tools[e]);
    $("#toolStatus").innerHTML =
      (engines.length
        ? `<span class="ok">✔ TeX engines: ${engines.join(", ")}</span>`
        : `<span class="missing">✘ No LaTeX engine found on this machine — install TeX Live, MiKTeX or MacTeX to compile.</span>`) +
      ` · latexmk: ${tools.latexmk ? '<span class="ok">yes</span>' : (tools.perl_missing || []).includes("latexmk") ? '<span class="missing">needs Perl</span>' : "no (built-in pipeline used)"}` +
      ((tools.perl_missing || []).length ? `<br><span class="muted">⚠ ${tools.perl_missing.join(", ")} need Perl, which isn't installed (usual with MiKTeX). Compiling still works with the built-in pipeline; install <a href="https://strawberryperl.com" target="_blank" rel="noopener">Strawberry Perl</a> and restart to enable latexmk and the changes PDF.</span>` : "") +
      ` · SyncTeX: ${tools.synctex ? '<span class="ok">yes</span>' : "no"}` +
      `<br><span class="muted">Projects are stored in <code>${status.projects_dir}</code></span>`;

    const projects = await api("GET", "/api/projects");
    renderProjectList(projects);
  }

  function updateTemplateDesc() {
    const t = templates.find((x) => x.id === $("#newProjectTemplate").value);
    $("#templateDesc").textContent = t ? t.description : "";
  }
  $("#newProjectTemplate").onchange = updateTemplateDesc;

  function renderProjectList(projects) {
    const tbody = $("#projectList");
    const filter = $("#projectFilter").value.toLowerCase();
    tbody.innerHTML = "";
    const shown = projects.filter((p) => p.name.toLowerCase().includes(filter));
    $("#noProjects").hidden = projects.length > 0;
    shown.forEach((p) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td><a></a></td><td class="muted">${fmtDate(p.modified)}</td>
        <td class="actions">
          <button data-act="rename" title="Rename">✎ Rename</button>
          <button data-act="copy" title="Duplicate">⧉ Copy</button>
          <a class="btn ghost" href="/api/projects/${enc(p.name)}/zip" title="Download zip" style="color:inherit">⤓</a>
          <button data-act="delete" title="Delete">🗑</button>
        </td>`;
      const a = tr.querySelector("a");
      a.textContent = p.name;
      a.onclick = () => openProject(p.name);
      tr.querySelector('[data-act="rename"]').onclick = async () => {
        const n = prompt("New project name:", p.name);
        if (!n || n === p.name) return;
        try { await api("POST", `/api/projects/${enc(p.name)}/rename`, { name: n }); loadDashboard(); }
        catch (e) { toast(e.message, true); }
      };
      tr.querySelector('[data-act="copy"]').onclick = async () => {
        const n = prompt("Name for the copy:", p.name + "-copy");
        if (!n) return;
        try { await api("POST", `/api/projects/${enc(p.name)}/duplicate`, { name: n }); loadDashboard(); }
        catch (e) { toast(e.message, true); }
      };
      tr.querySelector('[data-act="delete"]').onclick = async () => {
        if (!confirm(`Delete project "${p.name}" and all its files? This cannot be undone.`)) return;
        try { await api("DELETE", `/api/projects/${enc(p.name)}`); loadDashboard(); }
        catch (e) { toast(e.message, true); }
      };
      tbody.appendChild(tr);
    });
    $("#projectFilter").oninput = () => renderProjectList(projects);
  }

  $("#newProjectForm").onsubmit = async (ev) => {
    ev.preventDefault();
    const name = $("#newProjectName").value.trim();
    try {
      await api("POST", "/api/projects", { name, template: $("#newProjectTemplate").value });
      $("#newProjectName").value = "";
      openProject(name);
    } catch (e) { toast(e.message, true); }
  };

  $("#importZip").onchange = async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    const name = prompt("Project name:", file.name.replace(/\.zip$/i, "").replace(/[^A-Za-z0-9 _.\-]/g, "_"));
    if (!name) { ev.target.value = ""; return; }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    try {
      const r = await api("POST", "/api/projects/import", fd, true);
      openProject(r.name);
    } catch (e) { toast(e.message, true); }
    ev.target.value = "";
  };

  // ============================================================== EDITOR
  let editor = null;

  function initEditor() {
    if (editor) return;
    editor = CodeMirror($("#editorHost"), {
      mode: "stex",
      lineNumbers: true,
      lineWrapping: true,
      matchBrackets: true,
      autoCloseBrackets: { pairs: "()[]{}$$", explode: "[]{}" },
      styleActiveLine: true,
      indentUnit: 2,
      tabSize: 2,
      gutters: ["issue-gutter", "CodeMirror-linenumbers"],
      theme: document.body.classList.contains("dark") ? "material-darker" : "default",
      extraKeys: {
        "Ctrl-S": () => saveAndCompile(),
        "Cmd-S": () => saveAndCompile(),
        "Ctrl-Enter": () => saveAndCompile(),
        "Cmd-Enter": () => saveAndCompile(),
        "Ctrl-/": "toggleComment",
        "Cmd-/": "toggleComment",
        "Ctrl-B": () => wrapSelection("\\textbf{", "}"),
        "Cmd-B": () => wrapSelection("\\textbf{", "}"),
        "Ctrl-I": () => wrapSelection("\\textit{", "}"),
        "Cmd-I": () => wrapSelection("\\textit{", "}"),
        "Ctrl-Space": (cm) => cm.showHint({ hint: LatexHints.hint, completeSingle: false }),
        "Alt-G": "jumpToLine",
        Tab: (cm) => (cm.somethingSelected() ? cm.indentSelection("add") : cm.replaceSelection("  ")),
        "Shift-Tab": (cm) => cm.indentSelection("subtract"),
      },
    });

    // Autocomplete triggers. Uses "change" (not "inputRead") because the
    // auto-close-brackets addon consumes "{" before inputRead fires.
    const HINT_CONTEXT = /(\\[A-Za-z]+$)|(\\(begin|ref|eqref|autoref|cref|Cref|pageref|cite\w*|nocite)\*?(\[[^\]]*\])*\{[^}]*$)/;
    editor.on("change", (cm, change) => {
      if (change.origin !== "+input") return;
      const typed = change.text.join("");
      if (!/^[\\{,A-Za-z]|^\{\}$/.test(typed)) return;
      // Defer one tick so an open popup can close itself first (e.g. on "{").
      setTimeout(() => {
        if (cm.state.completionActive) return;
        const cur = cm.getCursor();
        const left = cm.getLine(cur.line).slice(0, cur.ch);
        if (typed === "\\" || HINT_CONTEXT.test(left)) {
          cm.showHint({ hint: LatexHints.hint, completeSingle: false });
        }
      }, 0);
    });
    editor.on("change", (cm, change) => {
      if (change.origin === "setValue") return;
      const tab = activeTab();
      if (!tab) return;
      tab.dirty = true;
      renderTabs();
      setSaveStatus("Unsaved changes…");
      autosave();
      refreshOutline();
      if ($("#autoCompile").checked) autoCompile();
    });
  }

  function wrapSelection(before, after) {
    const sel = editor.getSelection();
    editor.replaceSelection(before + sel + after);
    if (!sel) {
      const c = editor.getCursor();
      editor.setCursor({ line: c.line, ch: c.ch - after.length });
    }
  }

  const activeTab = () => state.openTabs.find((t) => t.path === state.active);

  function setSaveStatus(text) { $("#saveStatus").textContent = text; }

  // ------------------------------------------------------------ projects
  async function openProject(name) {
    await closeAllTabs(true);
    state.project = name;
    state.issues = [];
    state.collapsed = new Set();
    $("#dashboard").hidden = true;
    $("#editorView").hidden = false;
    $("#projectTitle").textContent = name;
    document.title = `${name} — Overleaf Copy`;
    history.replaceState(null, "", "/?project=" + enc(name));
    $("#downloadZip").href = `/api/projects/${enc(name)}/zip`;
    $("#downloadPdf").href = `/api/projects/${enc(name)}/pdf?download=1`;
    $("#pdfFrame").hidden = true;
    $("#pdfPlaceholder").hidden = false;
    $("#autoCompile").checked = localStorage.getItem("oc-auto-" + name) === "1";
    renderIssues(null);
    initEditor();
    await refreshTree();
    const main = state.settings.main;
    const lastOpen = JSON.parse(localStorage.getItem("oc-open-" + name) || "[]");
    const toOpen = lastOpen.length ? lastOpen : (main ? [main] : []);
    for (const p of toOpen) {
      if (findNode(p)) await openFile(p, true);
    }
    if (main && findNode(main) && !lastOpen.length) await openFile(main);
    else if (state.openTabs.length) activateTab(state.openTabs[0].path);
    renderTabs();
    refreshKeys();
    updateWordCount();
    // Show an existing PDF straight away if one was built before.
    if (state.hasPdf) showPdf();
    emit("projectOpened", name);
  }

  async function refreshTree() {
    const data = await api("GET", `/api/projects/${enc(state.project)}`);
    state.tree = data.tree;
    state.settings = data.settings;
    state.hasPdf = data.has_pdf;
    $("#engineSelect").value = data.settings.engine || "pdflatex";
    renderMainSelect();
    renderTree();
    emit("treeRefreshed", state.tree);
  }

  function allFiles(nodes = state.tree, out = []) {
    for (const n of nodes) {
      if (n.type === "file") out.push(n);
      else allFiles(n.children, out);
    }
    return out;
  }
  function findNode(path, nodes = state.tree) {
    for (const n of nodes) {
      if (n.path === path) return n;
      if (n.type === "folder") { const f = findNode(path, n.children); if (f) return f; }
    }
    return null;
  }

  function renderMainSelect() {
    const sel = $("#mainSelect");
    sel.innerHTML = "";
    const texFiles = allFiles().filter((f) => f.name.endsWith(".tex"));
    if (!texFiles.length) sel.innerHTML = "<option value=''>(no .tex file)</option>";
    texFiles.forEach((f) => {
      const o = document.createElement("option");
      o.value = f.path; o.textContent = "main: " + f.path;
      if (f.path === state.settings.main) o.selected = true;
      sel.appendChild(o);
    });
  }
  $("#mainSelect").onchange = async (ev) => {
    try { state.settings = await api("PUT", `/api/projects/${enc(state.project)}/settings`, { main: ev.target.value }); renderTree(); }
    catch (e) { toast(e.message, true); }
  };
  $("#engineSelect").onchange = async (ev) => {
    try { state.settings = await api("PUT", `/api/projects/${enc(state.project)}/settings`, { engine: ev.target.value }); toast("Engine set to " + ev.target.value); }
    catch (e) { toast(e.message, true); }
  };
  $("#autoCompile").onchange = (ev) => localStorage.setItem("oc-auto-" + state.project, ev.target.checked ? "1" : "0");
  $("#backToDash").onclick = async () => { await closeAllTabs(true); loadDashboard(); };

  // ------------------------------------------------------------ file tree
  function renderTree() {
    const host = $("#fileTree");
    host.innerHTML = "";
    host.appendChild(buildTreeDom(state.tree, 0));
    // Root drop target (move to top-level)
    host.ondragover = (e) => { e.preventDefault(); };
    host.ondrop = (e) => {
      if (e.target !== host && e.target.closest(".tree-item")) return;
      e.preventDefault();
      handleDrop(e, "");
    };
  }

  function buildTreeDom(nodes) {
    const frag = document.createDocumentFragment();
    nodes.forEach((n) => {
      const item = document.createElement("div");
      item.className = "tree-item" + (n.path === state.active ? " selected" : "");
      item.draggable = true;
      item.dataset.path = n.path;
      const isFolder = n.type === "folder";
      const collapsed = state.collapsed.has(n.path);
      const icon = isFolder ? (collapsed ? "📁" : "📂") : fileIcon(n.name);
      item.innerHTML = `<span>${icon}</span><span class="name"></span>` +
        (n.path === state.settings.main ? '<span class="main-badge">MAIN</span>' : "") +
        '<button class="menu-btn" title="More">⋮</button>';
      item.querySelector(".name").textContent = n.name;
      item.onclick = (e) => {
        if (e.target.classList.contains("menu-btn")) return;
        if (isFolder) {
          collapsed ? state.collapsed.delete(n.path) : state.collapsed.add(n.path);
          renderTree();
        } else {
          openFile(n.path);
        }
      };
      item.oncontextmenu = (e) => { e.preventDefault(); showContextMenu(e.clientX, e.clientY, n); };
      item.querySelector(".menu-btn").onclick = (e) => {
        e.stopPropagation();
        const r = e.target.getBoundingClientRect();
        showContextMenu(r.left, r.bottom, n);
      };
      item.ondragstart = (e) => { e.dataTransfer.setData("text/x-oc-path", n.path); };
      item.ondragover = (e) => { e.preventDefault(); item.classList.add("drop-target"); };
      item.ondragleave = () => item.classList.remove("drop-target");
      item.ondrop = (e) => {
        e.preventDefault(); e.stopPropagation();
        item.classList.remove("drop-target");
        const folder = isFolder ? n.path : n.path.split("/").slice(0, -1).join("/");
        handleDrop(e, folder);
      };
      frag.appendChild(item);
      if (isFolder) {
        const kids = document.createElement("div");
        kids.className = "tree-children" + (collapsed ? " collapsed" : "");
        kids.appendChild(buildTreeDom(n.children));
        frag.appendChild(kids);
      }
    });
    return frag;
  }

  async function handleDrop(e, folder) {
    const src = e.dataTransfer.getData("text/x-oc-path");
    if (src) {
      const name = src.split("/").pop();
      const dest = folder ? `${folder}/${name}` : name;
      if (dest === src || dest.startsWith(src + "/")) return;
      await moveEntry(src, dest);
    } else if (e.dataTransfer.files.length) {
      await uploadFiles(e.dataTransfer.files, folder);
    }
  }

  let menuEl = null;
  function hideMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
  document.addEventListener("click", hideMenu);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });

  function showContextMenu(x, y, node) {
    hideMenu();
    menuEl = document.createElement("div");
    menuEl.className = "context-menu";
    const items = [];
    const folderOf = node.type === "folder" ? node.path : node.path.split("/").slice(0, -1).join("/");
    if (node.type === "folder") {
      items.push(["New file here…", () => newEntry("file", node.path)]);
      items.push(["New folder here…", () => newEntry("folder", node.path)]);
      items.push(["Upload here…", () => { uploadTarget = node.path; $("#uploadInput").click(); }]);
    } else {
      if (node.name.endsWith(".tex")) items.push(["Set as main document", () => { $("#mainSelect").value = node.path; $("#mainSelect").onchange({ target: { value: node.path } }); }]);
      items.push(["Download", () => window.open(`/api/projects/${enc(state.project)}/raw/${node.path.split("/").map(enc).join("/")}`)]);
    }
    items.push(["Rename…", () => renameEntry(node)]);
    items.push(["Move to folder…", async () => {
      const dest = prompt("Move to folder (blank = project root):", folderOf);
      if (dest === null) return;
      const target = (dest.trim().replace(/^\/+|\/+$/g, "") ? dest.trim().replace(/^\/+|\/+$/g, "") + "/" : "") + node.name;
      if (target !== node.path) await moveEntry(node.path, target);
    }]);
    items.push(["Delete", () => deleteEntry(node), "danger"]);
    items.forEach(([label, fn, cls]) => {
      const d = document.createElement("div");
      d.textContent = label;
      if (cls) d.className = cls;
      d.onclick = (e) => { e.stopPropagation(); hideMenu(); fn(); };
      menuEl.appendChild(d);
    });
    document.body.appendChild(menuEl);
    const r = menuEl.getBoundingClientRect();
    menuEl.style.left = Math.min(x, window.innerWidth - r.width - 4) + "px";
    menuEl.style.top = Math.min(y, window.innerHeight - r.height - 4) + "px";
  }

  function currentFolder() {
    if (!state.active) return "";
    return state.active.split("/").slice(0, -1).join("/");
  }

  async function newEntry(type, folder) {
    folder = folder ?? currentFolder();
    const label = type === "folder" ? "New folder name:" : "New file name (e.g. sections/related-work.tex):";
    const name = prompt(label, type === "folder" ? "" : "untitled.tex");
    if (!name) return;
    const path = (folder ? folder + "/" : "") + name.trim().replace(/^\/+/, "");
    let content = "";
    if (type === "file" && path.endsWith(".tex")) {
      const base = name.split("/").pop().replace(/\.tex$/, "").replace(/[-_]/g, " ");
      content = `\\section{${base.charAt(0).toUpperCase() + base.slice(1)}}\n\\label{sec:${name.split("/").pop().replace(/\.tex$/, "")}}\n\n`;
    }
    try {
      await api("POST", `/api/projects/${enc(state.project)}/new`, { path, type, content });
      await refreshTree();
      if (type === "file") {
        await openFile(path);
        if (path.endsWith(".tex") && path !== state.settings.main)
          toast(`Created ${path}. Include it from your main file with \\input{${path.replace(/\.tex$/, "")}}`);
      }
    } catch (e) { toast(e.message, true); }
  }
  $("#newFileBtn").onclick = () => newEntry("file");
  $("#newFolderBtn").onclick = () => newEntry("folder");

  async function renameEntry(node) {
    const newName = prompt("Rename to:", node.name);
    if (!newName || newName === node.name) return;
    const parent = node.path.split("/").slice(0, -1).join("/");
    await moveEntry(node.path, (parent ? parent + "/" : "") + newName);
  }

  async function moveEntry(from, to) {
    // Save first so we don't lose edits on the old path.
    const tab = state.openTabs.find((t) => t.path === from);
    if (tab && tab.dirty) await saveTab(tab);
    try {
      await api("POST", `/api/projects/${enc(state.project)}/move`, { from, to });
      // Update any open tabs under the moved path.
      state.openTabs.forEach((t) => {
        if (t.path === from || t.path.startsWith(from + "/")) {
          const np = to + t.path.slice(from.length);
          if (state.active === t.path) state.active = np;
          t.path = np;
        }
      });
      await refreshTree();
      renderTabs();
      persistOpenTabs();
    } catch (e) { toast(e.message, true); }
  }

  async function deleteEntry(node) {
    if (!confirm(`Delete "${node.path}"${node.type === "folder" ? " and everything in it" : ""}?`)) return;
    try {
      await api("DELETE", `/api/projects/${enc(state.project)}/file?path=${enc(node.path)}`);
      const affected = state.openTabs.filter((t) => t.path === node.path || t.path.startsWith(node.path + "/"));
      for (const t of affected) { t.dirty = false; closeTab(t.path); }
      await refreshTree();
    } catch (e) { toast(e.message, true); }
  }

  let uploadTarget = null;
  $("#uploadBtn").onclick = () => { uploadTarget = null; $("#uploadInput").click(); };
  $("#uploadInput").onchange = async (ev) => {
    await uploadFiles(ev.target.files, uploadTarget ?? currentFolder());
    ev.target.value = "";
  };
  async function uploadFiles(files, folder) {
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    fd.append("folder", folder || "");
    try {
      const r = await api("POST", `/api/projects/${enc(state.project)}/upload`, fd, true);
      toast(`Uploaded ${r.saved.length} file(s)`);
      await refreshTree();
      refreshKeys();
    } catch (e) { toast(e.message, true); }
  }
  // Drag files from the OS onto the sidebar.
  $("#sidebar").addEventListener("dragover", (e) => e.preventDefault());

  // ----------------------------------------------------------------- tabs
  async function openFile(path, background) {
    let tab = state.openTabs.find((t) => t.path === path);
    if (!tab) {
      const node = findNode(path);
      if (!node) return;
      if (!node.text) {
        tab = { path, binary: true };
      } else {
        try {
          const data = await api("GET", `/api/projects/${enc(state.project)}/file?path=${enc(path)}`);
          tab = { path, doc: CodeMirror.Doc(data.content, "stex"), dirty: false, mtime: data.mtime };
        } catch (e) { toast(e.message, true); return; }
      }
      state.openTabs.push(tab);
      persistOpenTabs();
    }
    if (!background) activateTab(path);
    renderTabs();
  }

  function activateTab(path) {
    const tab = state.openTabs.find((t) => t.path === path);
    if (!tab) return;
    state.active = path;
    const host = $("#editorHost"), bin = $("#binaryPreview");
    $("#emptyEditor").hidden = true;
    if (tab.binary) {
      host.hidden = true;
      bin.hidden = false;
      const url = `/api/projects/${enc(state.project)}/raw/${path.split("/").map(enc).join("/")}`;
      const ext = path.split(".").pop().toLowerCase();
      if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) bin.innerHTML = `<img src="${url}?t=${Date.now()}" alt="">`;
      else if (ext === "pdf") bin.innerHTML = `<iframe src="${url}"></iframe>`;
      else bin.innerHTML = `<div>Binary file — <a href="${url}" download>download</a></div>`;
    } else {
      bin.hidden = true;
      host.hidden = false;
      editor.swapDoc(tab.doc);
      editor.refresh();
      editor.focus();
      markIssuesInEditor();
    }
    renderTabs();
    renderTree();
    refreshOutline();
    emit("tabActivated", tab);
  }

  function renderTabs() {
    const host = $("#tabs");
    host.innerHTML = "";
    state.openTabs.forEach((t) => {
      const el = document.createElement("div");
      el.className = "tab" + (t.path === state.active ? " active" : "") + (t.dirty ? " dirty" : "");
      el.title = t.path;
      el.innerHTML = `<span class="name"></span><button class="close" title="Close">×</button>`;
      el.querySelector(".name").textContent = t.path.split("/").pop();
      el.onclick = (e) => { if (!e.target.classList.contains("close")) activateTab(t.path); };
      el.onauxclick = (e) => { if (e.button === 1) closeTab(t.path); };
      el.querySelector(".close").onclick = () => closeTab(t.path);
      host.appendChild(el);
    });
    if (!state.openTabs.length) {
      $("#editorHost").hidden = true;
      $("#binaryPreview").hidden = true;
      $("#emptyEditor").hidden = false;
    }
  }

  async function closeTab(path) {
    const idx = state.openTabs.findIndex((t) => t.path === path);
    if (idx < 0) return;
    const tab = state.openTabs[idx];
    if (tab.dirty) await saveTab(tab);
    state.openTabs.splice(idx, 1);
    if (state.active === path) {
      const next = state.openTabs[idx] || state.openTabs[idx - 1];
      state.active = null;
      if (next) activateTab(next.path);
    }
    persistOpenTabs();
    renderTabs();
    renderTree();
    refreshOutline();
  }

  async function closeAllTabs(save) {
    if (save) await saveAll();
    state.openTabs = [];
    state.active = null;
    if (editor) editor.swapDoc(CodeMirror.Doc("", "stex"));
  }

  function persistOpenTabs() {
    if (!state.project) return;
    localStorage.setItem("oc-open-" + state.project, JSON.stringify(state.openTabs.map((t) => t.path)));
  }

  // ----------------------------------------------------------------- save
  // Saves are chained per tab so two saves of the same file never overlap.
  function saveTab(tab) {
    if (!tab || tab.binary) return Promise.resolve();
    tab.saveChain = (tab.saveChain || Promise.resolve()).catch(() => {}).then(() => doSave(tab));
    return tab.saveChain;
  }
  async function doSave(tab) {
    if (!tab.dirty) return;
    const content = tab.doc.getValue();
    const gen = tab.doc.changeGeneration();
    tab.saving = true;
    setSaveStatus("Saving…");
    try {
      await api("PUT", `/api/projects/${enc(state.project)}/file`, { path: tab.path, content });
      // Only mark clean if nothing was typed while the request was in flight.
      if (tab.doc.isClean(gen)) tab.dirty = false;
      emit("saved", tab);
      setSaveStatus("Saved " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    } catch (e) {
      setSaveStatus("⚠ Save failed");
      toast("Save failed: " + e.message, true);
      throw e;
    } finally {
      tab.saving = false;
      renderTabs();
    }
  }
  async function saveAll() {
    for (const t of state.openTabs) if (t.dirty) await saveTab(t);
  }
  const autosave = debounce(() => saveAll().then(() => refreshKeysDebounced()).catch(() => {}), 800);
  let autoCompileTimer;
  const autoCompile = () => {
    clearTimeout(autoCompileTimer);
    const delay = (window.OC && OC.settings && OC.settings.get().autoCompileDelay) || 2500;
    autoCompileTimer = setTimeout(() => compile(), delay);
  };
  window.addEventListener("beforeunload", (e) => {
    if (state.openTabs.some((t) => t.dirty)) { saveAll(); e.preventDefault(); e.returnValue = ""; }
  });

  async function saveAndCompile() {
    autosave.cancel();
    try { await saveAll(); } catch (_) { return; }
    compile();
  }

  // -------------------------------------------------------------- compile
  async function compile(clean) {
    if (!state.project) return;
    if (state.compiling) { state.pendingCompile = true; return; }
    autosave.cancel();
    try { await saveAll(); } catch (_) { return; }
    state.compiling = true;
    $("#compileBtn").disabled = true;
    $("#compilingOverlay").hidden = false;
    const project = state.project;
    try {
      const r = await api("POST", `/api/projects/${enc(project)}/compile`, { clean: !!clean });
      if (project !== state.project) return;
      state.issues = r.issues || [];
      renderIssues(r);
      markIssuesInEditor();
      emit("compiled", r);
      if (r.pdf) {
        if (window.OC.pdfViewer) showPdf(null, true);
        else showPdf(await cursorPdfPage());
      }
      if (!r.pdf || r.pdf_stale) {
        showLogs();
        toast("Compilation failed — see Logs & errors", true);
      } else if (!r.ok) {
        toast("Compiled with errors — PDF may be incomplete", true);
      }
      updateWordCount();
    } catch (e) {
      toast(e.message, true);
    } finally {
      state.compiling = false;
      $("#compileBtn").disabled = false;
      $("#compilingOverlay").hidden = true;
      if (state.pendingCompile) { state.pendingCompile = false; compile(); }
    }
  }
  $("#compileBtn").onclick = () => saveAndCompile();
  $("#cleanCompileBtn").onclick = () => compile(true);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "Enter") && state.project) {
      e.preventDefault();
      saveAndCompile();
    }
  });

  function showPdf(page, keepScroll) {
    const viewer = window.OC && window.OC.pdfViewer;
    if (viewer) {
      // PDF.js viewer: keeps scroll position across recompiles.
      $("#pdfFrame").hidden = true;
      $("#pdfPlaceholder").hidden = true;
      viewer.load(`/api/projects/${enc(state.project)}/pdf?t=${Date.now()}`, { page: keepScroll ? null : page, keepScroll });
      switchPreview("pdf");
      return;
    }
    const frame = $("#pdfFrame");
    const url = `/api/projects/${enc(state.project)}/pdf?t=${Date.now()}` + (page ? `#page=${page}` : "");
    frame.hidden = false;
    $("#pdfPlaceholder").hidden = true;
    frame.src = url;
    state.pdfPage = page || null;
    switchPreview("pdf");
  }
  function showLogs() { switchPreview("logs"); }
  function switchPreview(which) {
    $("#showPdf").classList.toggle("active", which === "pdf");
    $("#showLogs").classList.toggle("active", which === "logs");
    $("#pdfContainer").hidden = which !== "pdf";
    $("#logContainer").hidden = which !== "logs";
  }
  $("#showPdf").onclick = () => switchPreview("pdf");
  $("#showLogs").onclick = () => switchPreview("logs");
  $("#openPdfTab").onclick = () => window.open(`/api/projects/${enc(state.project)}/pdf?t=${Date.now()}`, "_blank");

  async function cursorPdfPage() {
    const tab = activeTab();
    if (!tab || tab.binary || !tab.path.endsWith(".tex")) return state.pdfPage;
    try {
      const line = editor.getCursor().line + 1;
      const r = await api("GET", `/api/projects/${enc(state.project)}/synctex?path=${enc(tab.path)}&line=${line}`);
      return r.page || state.pdfPage;
    } catch (_) { return state.pdfPage; }
  }
  $("#syncToPdf").onclick = async () => {
    const page = await cursorPdfPage();
    if (page) showPdf(page); else toast("Couldn't locate this line in the PDF (compile first; needs SyncTeX).");
  };

  function renderIssues(result) {
    const list = $("#issueList");
    list.innerHTML = "";
    const badge = $("#issueBadge");
    if (!result) { badge.hidden = true; $("#rawLog").textContent = ""; return; }
    const errors = state.issues.filter((i) => i.level === "error");
    const warnings = state.issues.filter((i) => i.level !== "error");
    badge.hidden = !state.issues.length;
    badge.textContent = errors.length || warnings.length;
    badge.classList.toggle("warn", !errors.length);

    const summary = document.createElement("div");
    summary.className = "issue-summary " + (errors.length || !result.pdf ? "bad" : "ok");
    summary.textContent = errors.length
      ? `${errors.length} error(s), ${warnings.length} warning(s)`
      : (result.pdf ? `✔ Compiled in ${result.seconds}s with ${result.engine}` + (warnings.length ? ` — ${warnings.length} warning(s)` : "")
                    : "✘ No PDF produced — see the raw log below");
    list.appendChild(summary);

    state.issues.forEach((iss) => {
      const el = document.createElement("div");
      el.className = "issue " + iss.level;
      el.innerHTML = `<div class="where"></div><div class="msg"></div>` + (iss.context ? "<pre></pre>" : "");
      el.querySelector(".where").textContent = `${iss.level.toUpperCase()} · ${iss.file || "?"}${iss.line ? ":" + iss.line : ""}`;
      el.querySelector(".msg").textContent = iss.message;
      if (iss.context) el.querySelector("pre").textContent = iss.context;
      el.onclick = () => gotoIssue(iss);
      list.appendChild(el);
    });
    $("#rawLog").textContent = (result.runner_log ? result.runner_log + "\n\n" : "") + (result.log || "");
  }

  async function gotoIssue(iss) {
    if (!iss.file || !findNode(iss.file)) { toast("Source file for this message is not in the project."); return; }
    await openFile(iss.file);
    if (iss.line) {
      const ln = iss.line - 1;
      editor.setCursor({ line: ln, ch: 0 });
      editor.scrollIntoView({ line: ln, ch: 0 }, 120);
      editor.focus();
    }
  }

  function markIssuesInEditor() {
    if (!editor) return;
    const tab = activeTab();
    editor.clearGutter("issue-gutter");
    for (let i = 0; i < editor.lineCount(); i++) {
      editor.removeLineClass(i, "background", "cm-error-line");
      editor.removeLineClass(i, "background", "cm-warning-line");
    }
    if (!tab || tab.binary) return;
    state.issues.filter((i) => i.file === tab.path && i.line && i.level !== "typesetting").forEach((iss) => {
      const ln = Math.min(iss.line - 1, editor.lineCount() - 1);
      const marker = document.createElement("div");
      marker.className = "issue-marker " + iss.level;
      marker.title = iss.message;
      editor.setGutterMarker(ln, "issue-gutter", marker);
      editor.addLineClass(ln, "background", iss.level === "error" ? "cm-error-line" : "cm-warning-line");
    });
  }

  // -------------------------------------------------------------- outline
  const refreshOutline = debounce(() => {
    const host = $("#outline");
    host.innerHTML = "";
    const tab = activeTab();
    if (!tab || tab.binary) return;
    const levels = { part: 0, chapter: 0, section: 1, subsection: 2, subsubsection: 3, paragraph: 4, frametitle: 2 };
    const re = /^\s*\\(part|chapter|section|subsection|subsubsection|paragraph|frametitle)\*?\s*(?:\[[^\]]*\])?\{(.*)\}/;
    tab.doc.eachLine((lineHandle) => {
      const m = lineHandle.text.match(re);
      if (!m) return;
      const lineNo = tab.doc.getLineNumber(lineHandle);
      const d = document.createElement("div");
      d.className = "lvl-" + levels[m[1]];
      d.textContent = m[2].replace(/\}.*$/, "") || "(untitled)";
      d.title = `Line ${lineNo + 1}`;
      d.onclick = () => { editor.setCursor({ line: lineNo, ch: 0 }); editor.scrollIntoView({ line: lineNo, ch: 0 }, 100); editor.focus(); };
      host.appendChild(d);
    });
    if (!host.children.length) host.innerHTML = '<div class="muted">No sections in this file.</div>';
  }, 300);

  // ------------------------------------------------- keys & word count
  async function refreshKeys() {
    if (!state.project) return;
    try { LatexHints.keys = await api("GET", `/api/projects/${enc(state.project)}/keys`); } catch (_) {}
  }
  const refreshKeysDebounced = debounce(refreshKeys, 3000);

  async function updateWordCount() {
    if (!state.project) return;
    try {
      const r = await api("GET", `/api/projects/${enc(state.project)}/wordcount`);
      $("#wordCount").textContent = `${r.words.toLocaleString()} words`;
      $("#wordCount").title = r.detail + "\n(click to refresh)";
    } catch (_) {}
  }
  $("#wordCount").onclick = updateWordCount;

  // ------------------------------------------------------- pane resizing
  document.querySelectorAll(".gutter").forEach((g) => {
    g.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const target = document.getElementById(g.dataset.resize);
      const startX = e.clientX;
      const startW = target.getBoundingClientRect().width;
      g.classList.add("dragging");
      // Iframes swallow mouse events while dragging; disable them temporarily.
      document.querySelectorAll("iframe").forEach((f) => (f.style.pointerEvents = "none"));
      const move = (ev) => {
        const w = Math.max(140, startW + ev.clientX - startX);
        target.style.flex = `0 0 ${w}px`;
        target.style.width = w + "px";
        if (editor) editor.refresh();
      };
      const up = () => {
        g.classList.remove("dragging");
        document.querySelectorAll("iframe").forEach((f) => (f.style.pointerEvents = ""));
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        localStorage.setItem("oc-w-" + target.id, target.style.width);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    const saved = localStorage.getItem("oc-w-" + g.dataset.resize);
    if (saved) {
      const t = document.getElementById(g.dataset.resize);
      t.style.flex = `0 0 ${saved}`;
      t.style.width = saved;
    }
  });

  // ------------------------------------------------- sidebar panels
  function addPanel(id, icon, title, el) {
    const btn = document.createElement("button");
    btn.dataset.panel = id; btn.textContent = icon; btn.title = title;
    $("#sidebarTabs").appendChild(btn);
    const wrap = document.createElement("div");
    wrap.className = "side-panel"; wrap.dataset.panel = id; wrap.hidden = true;
    const head = document.createElement("div");
    head.className = "panel-head"; head.innerHTML = "<span></span>";
    head.firstChild.textContent = title;
    wrap.appendChild(head);
    wrap.appendChild(el);
    $("#sidebar").appendChild(wrap);
    return wrap;
  }
  function showPanel(id) {
    document.querySelectorAll("#sidebarTabs button").forEach((b) => b.classList.toggle("active", b.dataset.panel === id));
    document.querySelectorAll(".side-panel").forEach((p) => (p.hidden = p.dataset.panel !== id));
    emit("panelShown", id);
  }
  $("#sidebarTabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) showPanel(b.dataset.panel);
  });

  // ---------------------------------------------------------- tools menu
  const toolGroups = [];
  function addTool(label, fn, group = "Tools") {
    toolGroups.push({ label, fn, group });
  }
  $("#toolsBtn").onclick = (e) => {
    e.stopPropagation();
    const menu = $("#toolsMenu");
    if (!menu.hidden) { menu.hidden = true; return; }
    menu.innerHTML = "";
    let lastGroup = null;
    toolGroups.forEach((t) => {
      if (t.group !== lastGroup) {
        const h = document.createElement("div"); h.className = "menu-group"; h.textContent = t.group;
        menu.appendChild(h); lastGroup = t.group;
      }
      const d = document.createElement("div"); d.className = "menu-item"; d.textContent = t.label;
      d.onclick = () => { menu.hidden = true; t.fn(); };
      menu.appendChild(d);
    });
    menu.hidden = false;
  };
  document.addEventListener("click", () => { $("#toolsMenu").hidden = true; });

  // --------------------------------------------------------------- modal
  // OC.modal({title, body: Node|string, buttons: [{label, primary, onClick(close)}], wide})
  function modal({ title, body, buttons = [], wide = false, onClose }) {
    const back = document.createElement("div");
    back.className = "modal-backdrop";
    const box = document.createElement("div");
    box.className = "modal" + (wide ? " wide" : "");
    box.innerHTML = '<div class="modal-head"><span></span><button class="modal-x" title="Close">×</button></div><div class="modal-body"></div><div class="modal-foot"></div>';
    box.querySelector(".modal-head span").textContent = title;
    const bodyEl = box.querySelector(".modal-body");
    if (typeof body === "string") bodyEl.innerHTML = body; else if (body) bodyEl.appendChild(body);
    const close = () => { back.remove(); document.removeEventListener("keydown", esc); if (onClose) onClose(); };
    const esc = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", esc);
    box.querySelector(".modal-x").onclick = close;
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    const foot = box.querySelector(".modal-foot");
    buttons.forEach((b) => {
      const btn = document.createElement("button");
      btn.className = "btn" + (b.primary ? " primary" : "");
      btn.textContent = b.label;
      btn.onclick = () => b.onClick(close, btn);
      foot.appendChild(btn);
    });
    if (!buttons.length) foot.remove();
    back.appendChild(box);
    $("#modalRoot").appendChild(back);
    return { close, body: bodyEl, box };
  }

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function gotoLine(path, line, ch = 0) {
    await openFile(path);
    if (line) {
      const ln = line - 1;
      editor.setCursor({ line: ln, ch });
      editor.scrollIntoView({ line: ln, ch }, 120);
      editor.focus();
    }
  }

  async function reloadOpenTabs() {
    for (const t of state.openTabs) {
      if (t.binary || t.dirty) continue;
      try {
        const data = await api("GET", `/api/projects/${enc(state.project)}/file?path=${enc(t.path)}`);
        if (data.content !== t.doc.getValue()) {
          const cur = t.doc.getCursor();
          t.doc.setValue(data.content);
          t.doc.setCursor(cur);
          t.doc.markClean();
          t.dirty = false;
        }
      } catch (_) { /* file may have been removed */ }
    }
    renderTabs();
  }

  // Public API for feature modules.
  window.OC = {
    state, api, enc, toast, debounce, on, emit, modal, el, escapeHtml,
    addPanel, showPanel, addTool,
    get editor() { return editor; },
    activeTab, openFile, gotoLine, refreshTree, reloadOpenTabs, saveAll, compile, openProject,
    showPdf, showLogs, switchPreview, allFiles, findNode, currentFolder, uploadFiles,
    projectUrl: (suffix = "") => `/api/projects/${enc(state.project)}${suffix}`,
  };

  // ------------------------------------------------------------- startup
  applyTheme(localStorage.getItem("oc-dark") === "1");
  window.addEventListener("DOMContentLoaded", () => {
  emit("init");
  const params = new URLSearchParams(location.search);
  if (params.get("project")) {
    openProject(params.get("project")).catch((e) => { toast(e.message, true); loadDashboard(); });
  } else {
    loadDashboard().catch((e) => toast("Could not reach the server: " + e.message, true));
  }
  });
})();
