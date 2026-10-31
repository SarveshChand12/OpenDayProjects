/* Writing stats: words per section, words written per day (last 30 days), a daily goal,
 * a target length, and a deadline countdown shown in the top bar.
 */
(() => {
  const OC = window.OC;

  // Top-bar chip: "ICML: 12 days · 340/500 today"
  const chip = OC.el('<span id="goalChip" class="goal-chip" title="Writing stats & goals" hidden></span>');
  document.querySelector("#wordCount").after(chip);
  chip.onclick = () => openStats();

  async function refreshChip() {
    if (!OC.state.project) return;
    try {
      const r = await OC.api("GET", OC.projectUrl("/stats"));
      updateChip(r);
    } catch (_) { chip.hidden = true; }
  }
  function updateChip(r) {
    const g = r.goals || {};
    const parts = [];
    if (r.deadline_days !== null && r.deadline_days !== undefined) {
      const d = r.deadline_days;
      const when = d < 0 ? `${-d} day${d === -1 ? "" : "s"} ago` : d === 0 ? "today!" : `${d} day${d === 1 ? "" : "s"}`;
      parts.push(`⏰ ${g.deadline_label || "Deadline"}: ${when}`);
    }
    if (g.daily_goal) parts.push(`${Math.max(0, r.today_written).toLocaleString()}/${g.daily_goal.toLocaleString()} today`);
    chip.textContent = parts.join(" · ");
    chip.hidden = !parts.length;
    chip.classList.toggle("urgent", r.deadline_days !== null && r.deadline_days <= 3);
    chip.classList.toggle("done", !!g.daily_goal && r.today_written >= g.daily_goal);
  }

  // ------------------------------------------------------------ chart (SVG)
  function barChart(days, goal) {
    const W = 680, H = 200, padL = 44, padR = 8, padT = 12, padB = 26;
    const values = days.map((d) => Math.max(0, d.written));
    const max = Math.max(goal || 0, ...values, 10);
    const nice = (v) => { const p = 10 ** Math.floor(Math.log10(v)); return Math.ceil(v / p) * p; };
    const top = nice(max);
    const iw = W - padL - padR, ih = H - padT - padB;
    const bw = iw / days.length;
    const barW = Math.max(3, bw - 2);                // 2px surface gap between bars
    const y = (v) => padT + ih - (v / top) * ih;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "viz");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Words written per day over the last 30 days");
    const add = (tag, attrs, text) => {
      const e = document.createElementNS(NS, tag);
      Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
      if (text !== undefined) e.textContent = text;
      svg.appendChild(e); return e;
    };
    // recessive grid + y labels
    [0, 0.5, 1].forEach((f) => {
      const v = Math.round(top * f);
      add("line", { x1: padL, x2: W - padR, y1: y(v), y2: y(v), class: "viz-grid" });
      add("text", { x: padL - 6, y: y(v) + 4, class: "viz-axis", "text-anchor": "end" }, v.toLocaleString());
    });
    // bars: rounded 4px data-end, square at the baseline
    days.forEach((d, i) => {
      const v = Math.max(0, d.written);
      const x = padL + i * bw + (bw - barW) / 2;
      if (v > 0) {
        const h = Math.max(2, y(0) - y(v));
        const r = Math.min(4, barW / 2, h);
        const top0 = y(0) - h;
        add("path", {
          class: "viz-bar",
          d: `M${x},${y(0)} V${top0 + r} Q${x},${top0} ${x + r},${top0} H${x + barW - r} Q${x + barW},${top0} ${x + barW},${top0 + r} V${y(0)} Z`,
        });
      }
      // hit target: full column, bigger than the mark
      const hit = add("rect", { x: padL + i * bw, y: padT, width: bw, height: ih, class: "viz-hit", tabindex: 0 });
      hit.dataset.i = i;
      if ((days.length - 1 - i) % 7 === 0) {          // weekly ticks anchored on today
        const lab = new Date(d.date + "T00:00").toLocaleDateString([], { month: "short", day: "numeric" });
        add("text", { x: padL + i * bw + bw / 2, y: H - 8, class: "viz-axis", "text-anchor": "middle" }, lab);
      }
    });
    add("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), class: "viz-baseline" });
    if (goal) {
      add("line", { x1: padL, x2: W - padR, y1: y(goal), y2: y(goal), class: "viz-goal" });
      add("text", { x: W - padR, y: y(goal) - 4, class: "viz-axis", "text-anchor": "end" }, `daily goal ${goal.toLocaleString()}`);
    }
    // tooltip (built with textContent)
    const wrap = document.createElement("div");
    wrap.className = "viz-wrap";
    wrap.appendChild(svg);
    const tip = document.createElement("div");
    tip.className = "viz-tip"; tip.hidden = true;
    const tv = document.createElement("strong"), tl = document.createElement("div");
    tip.append(tv, tl);
    wrap.appendChild(tip);
    const show = (e) => {
      const i = +e.target.dataset.i;
      if (Number.isNaN(i)) return;
      const d = days[i];
      tv.textContent = `${d.written.toLocaleString()} words`;
      tl.textContent = new Date(d.date + "T00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) + (d.total != null ? ` · total ${d.total.toLocaleString()}` : "");
      tip.hidden = false;
      const r = e.target.getBoundingClientRect(), wr = wrap.getBoundingClientRect();
      tip.style.left = Math.min(wr.width - 160, Math.max(0, r.left - wr.left + r.width / 2 - 70)) + "px";
      tip.style.top = "0px";
      svg.querySelectorAll(".viz-hit").forEach((h) => h.classList.toggle("active", h === e.target));
    };
    svg.addEventListener("pointermove", show);
    svg.addEventListener("focusin", show);
    svg.addEventListener("pointerleave", () => { tip.hidden = true; svg.querySelectorAll(".viz-hit").forEach((h) => h.classList.remove("active")); });
    return wrap;
  }

  // ------------------------------------------------------------ modal
  async function openStats() {
    await OC.saveAll();
    let r;
    try { r = await OC.api("GET", OC.projectUrl("/stats")); } catch (e) { return OC.toast(e.message, true); }
    updateChip(r);
    const g = r.goals || {};
    const wrap = document.createElement("div");
    const pct = g.target_words ? Math.min(100, Math.round((r.total / g.target_words) * 100)) : null;
    wrap.appendChild(OC.el(`<div class="stat-tiles">
      <div class="stat"><div class="num">${r.total.toLocaleString()}</div><div class="lbl">words in the paper${g.target_words ? ` · ${pct}% of ${g.target_words.toLocaleString()}` : ""}</div></div>
      <div class="stat"><div class="num">${r.today_written.toLocaleString()}</div><div class="lbl">net words today${g.daily_goal ? ` · goal ${g.daily_goal.toLocaleString()}` : ""}</div></div>
      <div class="stat"><div class="num">${r.deadline_days ?? "—"}</div><div class="lbl">${r.deadline_days != null ? `days until ${OC.escapeHtml(g.deadline_label || "deadline")} (${g.deadline})` : "no deadline set"}</div></div>
    </div>`));
    if (pct !== null) wrap.appendChild(OC.el(`<div class="progress" title="${pct}%"><div style="width:${pct}%"></div></div>`));

    wrap.appendChild(OC.el('<h3 class="viz-title">Words written per day — last 30 days</h3>'));
    wrap.appendChild(barChart(r.days, g.daily_goal));
    const tbl = OC.el(`<details><summary>Show as table</summary><table><tr><th>Date</th><th>Written</th><th>Total</th></tr>
      ${r.days.slice().reverse().map((d) => `<tr><td>${d.date}</td><td>${d.written}</td><td>${d.total ?? ""}</td></tr>`).join("")}</table></details>`);
    wrap.appendChild(tbl);

    wrap.appendChild(OC.el('<h3 class="viz-title">Words per section</h3>'));
    const sec = OC.el(`<table><tr><th>Section</th><th style="text-align:right">Words</th><th></th></tr></table>`);
    const maxW = Math.max(1, ...r.sections.map((s) => s.words));
    let m;
    r.sections.forEach((s) => {
      const row = OC.el(`<tr class="clickable"><td style="padding-left:${Math.max(0, s.level - 1) * 16 + 8}px"></td><td style="text-align:right">${s.words.toLocaleString()}</td>
        <td style="width:40%"><div class="sec-bar" style="width:${(s.words / maxW) * 100}%"></div></td></tr>`);
      row.firstElementChild.textContent = s.title;
      row.onclick = () => { m.close(); OC.gotoLine(s.path, s.line); };
      sec.appendChild(row);
    });
    wrap.appendChild(sec);

    const form = OC.el(`<div class="row" style="margin-top:14px">
      <label>Daily goal <input name="daily" type="number" min="0" step="50" value="${g.daily_goal || ""}" style="width:90px"></label>
      <label>Target length <input name="target" type="number" min="0" step="500" value="${g.target_words || ""}" style="width:100px"></label>
      <label>Deadline <input name="deadline" type="date" value="${g.deadline || ""}" style="width:auto"></label>
      <input name="label" placeholder="e.g. ICML submission" value="${OC.escapeHtml(g.deadline_label || "")}" style="flex:1; min-width:140px">
    </div>`);
    wrap.appendChild(form);
    m = OC.modal({
      title: "Writing stats", body: wrap, wide: true,
      buttons: [{ label: "Save goals", primary: true, onClick: async (close) => {
        const q = (n) => form.querySelector(`[name=${n}]`).value;
        try {
          await OC.api("PUT", OC.projectUrl("/stats/goals"), { daily_goal: q("daily"), target_words: q("target"), deadline: q("deadline"), deadline_label: q("label") });
          close(); refreshChip(); OC.toast("Goals saved");
        } catch (e) { OC.toast(e.message, true); }
      } }],
    });
  }

  OC.addTool("Writing stats & goals…", openStats, "Writing");
  OC.on("projectOpened", refreshChip);
  OC.on("compiled", refreshChip);
  OC.writingStats = { open: openStats };
})();
