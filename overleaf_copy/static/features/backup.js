/* Backup: one click (or automatically after compiling) to a folder and/or a git remote. */
(() => {
  const OC = window.OC;

  async function backupNow() {
    await OC.saveAll();
    OC.toast("Backing up…");
    try {
      const r = await OC.api("POST", OC.projectUrl("/backup/run"));
      if (r.ok) OC.toast("✔ Backup complete");
      else OC.modal({ title: "Backup had problems", body: `<pre>${OC.escapeHtml(r.messages.join("\n\n"))}</pre>`, wide: true });
    } catch (e) {
      if (/Set a backup folder/.test(e.message)) settingsDialog();
      else OC.toast(e.message, true);
    }
  }

  async function settingsDialog() {
    const c = await OC.api("GET", OC.projectUrl("/backup"));
    const last = c.last
      ? `<p class="hint">Last backup: ${new Date(c.last.time * 1000).toLocaleString()} — ${c.last.ok ? "✔ OK" : "✘ failed"}</p><pre>${OC.escapeHtml((c.last.messages || []).join("\n").slice(0, 1500))}</pre>`
      : '<p class="hint">No backups yet.</p>';
    const form = OC.el(`<div>
      <label class="field">Backup folder (a dated .zip is written here each time)</label>
      <input name="folder" placeholder="/Users/you/Dropbox/paper-backups   or   C:\\Users\\you\\OneDrive\\papers" value="${OC.escapeHtml(c.folder || "")}">
      <div class="row"><label>Keep the newest <input name="keep" type="number" min="1" value="${c.keep || 30}" style="width:70px"> zips</label></div>
      <label class="field">Git (version control + off-site copy)</label>
      ${c.git_available ? "" : '<p class="issue error">git is not installed on this computer.</p>'}
      <label><input type="checkbox" name="git_local" ${c.git_local || c.git_remote ? "checked" : ""}> Commit each backup to a git repository in the project folder</label>
      <input name="git_remote" placeholder="Optional remote to push to, e.g. git@github.com:you/my-paper.git (make it private!)" value="${OC.escapeHtml(c.git_remote || "")}" style="margin-top:6px">
      <div class="row"><label>Branch <input name="git_branch" value="${OC.escapeHtml(c.git_branch || "main")}" style="width:120px"></label></div>
      <div class="hint">Pushing uses your computer's normal git credentials (SSH key or credential manager).</div>
      <label class="field"><input type="checkbox" name="auto" ${c.auto ? "checked" : ""}> Back up automatically after compiling, at most every
        <input name="auto_minutes" type="number" min="5" value="${c.auto_minutes || 30}" style="width:70px"> minutes</label>
      ${last}
    </div>`);
    const f = (n) => form.querySelector(`[name=${n}]`);
    OC.modal({
      title: "Backup settings", body: form, wide: true,
      buttons: [
        { label: "Save", onClick: (close) => save(close, false) },
        { label: "Save & back up now", primary: true, onClick: (close) => save(close, true) },
      ],
    });
    async function save(close, run) {
      try {
        await OC.api("PUT", OC.projectUrl("/backup"), {
          folder: f("folder").value, keep: f("keep").value, git_local: f("git_local").checked,
          git_remote: f("git_remote").value, git_branch: f("git_branch").value,
          auto: f("auto").checked, auto_minutes: f("auto_minutes").value,
        });
        close();
        if (run) backupNow(); else OC.toast("Backup settings saved");
      } catch (e) { OC.toast(e.message, true); }
    }
  }

  OC.addTool("Back up now", backupNow, "Backup");
  OC.addTool("Backup settings…", settingsDialog, "Backup");
})();
