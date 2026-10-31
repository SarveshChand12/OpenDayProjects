/* Optional AI writing help (Claude). Select text, then Tools → AI assistant (or Ctrl/Cmd+Shift+A).
 * Results are shown for review — nothing changes in your document until you click "Replace".
 */
(() => {
  const OC = window.OC;

  async function status() { return OC.api("GET", "/api/ai/status"); }

  async function settingsDialog() {
    const s = await status();
    const form = OC.el(`<div>
      <p>The AI assistant sends <b>only the text you select</b> (and, if enabled, its surrounding paragraph)
      to Anthropic's Claude API when you click an action. It needs an Anthropic API key
      (<a href="https://console.anthropic.com/" target="_blank" rel="noopener">console.anthropic.com</a>).</p>
      ${s.sdk_installed ? "" : '<p class="issue error">The Anthropic SDK isn\'t installed. Run <code>pip install -r requirements-ai.txt</code> and restart.</p>'}
      <label class="field">API key</label>
      <input name="key" type="password" placeholder="${s.key_stored ? "•••••••• (stored) — leave blank to keep" : s.env_key ? "Using ANTHROPIC_API_KEY from the environment" : "sk-ant-…"}">
      <div class="hint">Stored in <code>~/.overleaf_copy/config.json</code> on this computer (owner-only permissions), never inside your projects.</div>
      <label class="field">Model</label><input name="model" value="${OC.escapeHtml(s.model)}">
      <label class="field">Effort (higher = more careful, slower, costs more)</label>
      <select name="effort">${["low", "medium", "high", "xhigh", "max"].map((e) => `<option ${e === s.effort ? "selected" : ""}>${e}</option>`).join("")}</select>
      <label style="display:block;margin-top:10px"><input type="checkbox" name="ctx" ${s.send_context ? "checked" : ""}> Send the surrounding paragraph for context</label>
    </div>`);
    const f = (n) => form.querySelector(`[name=${n}]`);
    OC.modal({
      title: "AI assistant settings", body: form,
      buttons: [
        ...(s.key_stored ? [{ label: "Forget stored key", onClick: async (close) => { await OC.api("PUT", "/api/ai/settings", { api_key: "" }); close(); OC.toast("Key removed"); } }] : []),
        { label: "Save", primary: true, onClick: async (close) => {
          const body = { model: f("model").value, effort: f("effort").value, send_context: f("ctx").checked };
          if (f("key").value.trim()) body.api_key = f("key").value.trim();
          await OC.api("PUT", "/api/ai/settings", body);
          close(); OC.toast("AI settings saved");
        } },
      ],
    });
  }

  function surrounding(cm, from, to) {
    // The paragraph(s) around the selection: expand to blank lines, max ~40 lines each way.
    let a = from.line, b = to.line;
    for (let i = 0; i < 40 && a > 0 && cm.getLine(a - 1).trim() !== ""; i++) a--;
    for (let i = 0; i < 40 && b < cm.lineCount() - 1 && cm.getLine(b + 1).trim() !== ""; i++) b++;
    return cm.getRange({ line: a, ch: 0 }, { line: b, ch: cm.getLine(b).length });
  }

  async function assistant(presetAction) {
    const cm = OC.editor;
    const tab = OC.activeTab();
    if (!cm || !tab || tab.binary) return OC.toast("Open a file and select some text first.");
    let from = cm.getCursor("from"), to = cm.getCursor("to");
    if (!cm.somethingSelected()) {
      // No selection: use the current paragraph.
      // Stop at blank lines and structural commands (\documentclass, \begin{document}, \section…).
      const boundary = (l) => l.trim() === "" || /^\s*\\(documentclass|usepackage|begin\{document\}|end\{document\}|maketitle|(sub)*section|chapter|paragraph)\b/.test(l);
      let a = from.line, b = from.line;
      while (a > 0 && !boundary(cm.getLine(a - 1)) && !boundary(cm.getLine(a))) a--;
      while (b < cm.lineCount() - 1 && !boundary(cm.getLine(b + 1))) b++;
      from = { line: a, ch: 0 }; to = { line: b, ch: cm.getLine(b).length };
    }
    const hadSelection = cm.somethingSelected();
    const text = cm.getRange(from, to);
    if (!text.trim()) return OC.toast("Select some text first.");
    const mark = cm.markText(from, to, { className: "cm-ai-target" });
    const form = OC.el(`<div>
      <div class="row" style="margin-top:0">
        ${[["rephrase", "Rephrase"], ["tighten", "Make concise"], ["academic", "More academic"], ["grammar", "Fix grammar"], ["simplify", "Simplify"], ["abstract", "Improve abstract"]]
          .map(([a, l]) => `<button class="btn" data-a="${a}">${l}</button>`).join("")}
      </div>
      <div class="row"><input name="instr" placeholder="Or tell Claude what to do, e.g. 'turn this into a bulleted list'" style="flex:1"><button class="btn" data-a="custom">Ask</button></div>
      <div style="display:flex; gap:12px">
        <div style="flex:1"><label class="field">Selected text</label><pre class="ai-src"></pre></div>
        <div style="flex:1"><label class="field">Suggestion <span class="ai-model muted"></span></label><pre class="ai-out muted">Choose an action above.</pre></div>
      </div></div>`);
    form.querySelector(".ai-src").textContent = text;
    let result = null;
    const out = form.querySelector(".ai-out");
    const run = async (action) => {
      out.textContent = "Thinking…"; out.classList.add("muted");
      form.querySelectorAll("button[data-a]").forEach((b) => (b.disabled = true));
      try {
        const r = await OC.api("POST", "/api/ai/run", { action, text, instruction: form.querySelector("[name=instr]").value, context: hadSelection ? surrounding(cm, from, to) : "" });
        result = r.result;
        out.textContent = r.result + (r.truncated ? "\n\n[output was cut off]" : "");
        out.classList.remove("muted");
        form.querySelector(".ai-model").textContent = `(${r.model})`;
      } catch (e) {
        result = null;
        out.textContent = e.message;
        if (/API key|SDK/.test(e.message)) setTimeout(settingsDialog, 300);
      } finally { form.querySelectorAll("button[data-a]").forEach((b) => (b.disabled = false)); }
    };
    form.addEventListener("click", (e) => { const a = e.target.dataset && e.target.dataset.a; if (a) run(a); });
    form.querySelector("[name=instr]").addEventListener("keydown", (e) => { if (e.key === "Enter") run("custom"); });
    OC.modal({
      title: "✨ AI writing assistant", body: form, wide: true,
      onClose: () => mark.clear(),
      buttons: [
        { label: "Settings…", onClick: () => settingsDialog() },
        { label: "Copy", onClick: () => { if (result) navigator.clipboard.writeText(result).then(() => OC.toast("Copied")); } },
        { label: "Insert below", onClick: (close) => {
          const r = mark.find(); if (!result || !r) return;
          cm.replaceRange("\n\n" + result, r.to); close();
        } },
        { label: "Replace selection", primary: true, onClick: (close) => {
          const r = mark.find(); if (!result || !r) return;
          cm.replaceRange(result, r.from, r.to); close(); OC.toast("Replaced — Ctrl+Z to undo");
        } },
      ],
    });
    if (presetAction) run(presetAction);
  }

  // "Ask AI" button under each compile error.
  OC.on("compiled", () => {
    const els = document.querySelectorAll("#issueList .issue.error");
    const errors = OC.state.issues.filter((i) => i.level === "error");
    els.forEach((el, i) => {
      const iss = errors[i];
      if (!iss || el.querySelector(".ai-ask")) return;
      const b = OC.el('<button class="btn ai-ask" style="margin-top:6px;padding:2px 8px">✨ Ask AI to explain</button>');
      b.onclick = async (e) => {
        e.stopPropagation();
        let code = "";
        if (iss.file && iss.line && OC.findNode(iss.file)) {
          try {
            const d = await OC.api("GET", OC.projectUrl(`/file?path=${OC.enc(iss.file)}`));
            const lines = d.content.split("\n");
            code = lines.slice(Math.max(0, iss.line - 6), iss.line + 4).map((l, k) => `${Math.max(1, iss.line - 5) + k}: ${l}`).join("\n");
          } catch (_) {}
        }
        b.disabled = true; b.textContent = "Thinking…";
        try {
          const r = await OC.api("POST", "/api/ai/run", { action: "explain_error", text: `Error: ${iss.message}\n${iss.context || ""}\n\nSource (${iss.file}):\n${code}` });
          const box = OC.el('<div class="help"></div>');
          box.textContent = "✨ " + r.result;
          el.appendChild(box);
          b.remove();
        } catch (err) { b.disabled = false; b.textContent = "✨ Ask AI to explain"; OC.toast(err.message, true); }
      };
      el.appendChild(b);
    });
  });

  OC.addTool("✨ AI writing assistant (Ctrl+Shift+A)", () => assistant(), "AI");
  OC.addTool("AI assistant settings…", settingsDialog, "AI");
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "A" || e.key === "a") && OC.state.project) { e.preventDefault(); assistant(); }
  });
})();
