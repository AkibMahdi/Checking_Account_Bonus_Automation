/* Bank Bonus Planner — static UI.
 * Reads dist/offers.json (local, falling back to the jsDelivr CDN) and runs a
 * trimmed port of scripts/planner.py entirely in the browser. Config lives in
 * localStorage; nothing is ever sent anywhere. */
(() => {
  "use strict";

  const LOCAL = ["dist/offers.json", "../dist/offers.json"];
  const CDN = "https://cdn.jsdelivr.net/gh/AkibMahdi/Checking_Account_Bonus_Automation@main/dist/offers.json";
  const KEY = "bbp.config.v1";
  const DAY = 86400000;
  const ACH_SETTLE_DAYS = 1, DEFAULT_PAYOUT_DAYS = 60, ASSUMED_HOLD_DAYS = 180, APY = 0.04;

  const DEFAULTS = {
    state: "NY", minBonus: 150, concurrent: 3, capital: 20000,
    cadence: "biweekly", nextPay: isoDate(new Date(Date.now() + 7 * DAY)), ddAmount: 2400,
    splittable: true, business: false, chex: false, highOnly: false,
  };

  let offers = [];
  let view = "plan";
  let config = load();

  // ---------------------------------------------------------------- utils
  function isoDate(d) { return new Date(d).toISOString().slice(0, 10); }
  function parseDate(s) { return s ? new Date(s + "T00:00:00Z") : null; }
  function addDays(d, n) { return new Date(d.getTime() + n * DAY); }
  function addMonths(d, n) {
    const out = new Date(d.getTime());
    const day = out.getUTCDate();
    out.setUTCMonth(out.getUTCMonth() + n);
    if (out.getUTCDate() < day) out.setUTCDate(0);
    return out;
  }
  const money = n => "$" + Math.round(n).toLocaleString("en-US");
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function get(obj, path, fallback = null) {
    return path.split(".").reduce((o, k) => (o && o[k] !== undefined && o[k] !== null ? o[k] : null), obj) ?? fallback;
  }

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS };
    } catch (_) { return { ...DEFAULTS }; }
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(config)); } catch (_) { /* private mode */ }
  }

  // ---------------------------------------------------------------- pay dates
  function payDates(start, cadence, end) {
    const out = [];
    let cursor = new Date(start.getTime());
    if (cadence === "weekly" || cadence === "biweekly") {
      const step = cadence === "weekly" ? 7 : 14;
      while (cursor <= end) { out.push(new Date(cursor.getTime())); cursor = addDays(cursor, step); }
    } else if (cadence === "monthly") {
      let n = 0;
      while (cursor <= end) { out.push(new Date(cursor.getTime())); cursor = addMonths(start, ++n); }
    } else {
      const firstDay = start.getUTCDate() <= 15 ? start.getUTCDate() : start.getUTCDate() - 15;
      let month = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
      while (month <= end) {
        for (const day of [firstDay, firstDay + 15]) {
          const last = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + 1, 0)).getUTCDate();
          const d = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), Math.min(day, last)));
          if (d >= start && d <= end) out.push(d);
        }
        month = addMonths(month, 1);
      }
      out.sort((a, b) => a - b);
    }
    return out;
  }

  // ---------------------------------------------------------------- tiers
  function minCapital(offer) {
    const base = get(offer, "requirements.min_balance.amount", 0) || 0;
    const tiers = get(offer, "bonus.tiers");
    if (!tiers || get(offer, "bonus.tier_mode") !== "alternative") return base;
    return Math.min(...tiers.map(t => (t.min_balance ?? base)));
  }

  function variants(offer, capital) {
    const tiers = get(offer, "bonus.tiers");
    const mode = get(offer, "bonus.tier_mode");
    if (!tiers) return [[offer, null]];
    const clone = () => JSON.parse(JSON.stringify(offer));

    if (mode === "additive") {
      const first = tiers.reduce((a, b) => ((a.window_days ?? 1e9) <= (b.window_days ?? 1e9) ? a : b));
      const v = clone();
      v.bonus.amount = first.amount;
      const dd = v.requirements.direct_deposit;
      if (first.dd_cumulative != null) dd.min_amount_cumulative = first.dd_cumulative;
      if (first.dd_count != null) dd.count = first.dd_count;
      if (first.window_days) dd.window_days = first.window_days;
      const later = tiers.filter(t => t !== first).reduce((s, t) => s + t.amount, 0);
      return [[v, `tiers are additive: ${money(first.amount)} near-term, another ${money(later)} only over the full anniversary schedule`]];
    }
    if (mode === "repeating") {
      const t = tiers[0], cycles = t.repeatable_cycles || 1;
      const v = clone();
      v.bonus.amount = t.amount * cycles;
      v.requirements.direct_deposit.window_days = Math.min((t.window_days || 30) * cycles, 730);
      return [[v, `${money(t.amount)} per statement cycle x ${cycles} cycles — a multi-month commitment`]];
    }
    const out = [];
    for (const t of [...tiers].sort((a, b) => b.amount - a.amount)) {
      if (t.amount <= 0) continue;
      const need = t.min_balance ?? (get(offer, "requirements.min_balance.amount", 0) || 0);
      if (need && capital != null && need > capital) continue;
      const v = clone();
      v.bonus.amount = t.amount;
      const dd = v.requirements.direct_deposit;
      if (t.dd_cumulative != null) { dd.min_amount_cumulative = t.dd_cumulative; dd.required = true; }
      if (t.dd_count != null) dd.count = t.dd_count;
      if (t.window_days) dd.window_days = t.window_days;
      if (need) {
        v.requirements.min_balance = Object.assign({ fund_within_days: t.window_days, hold_days: null },
          v.requirements.min_balance || {}, { amount: need });
      } else if (t.dd_cumulative != null) {
        v.requirements.min_balance = null;
      }
      const note = t.amount !== get(offer, "bonus.amount")
        ? `planned at the ${money(t.amount)} tier, not the ${money(get(offer, "bonus.amount"))} headline: ${t.condition}`
        : null;
      out.push([v, note]);
    }
    return out.length ? out : [[offer, null]];
  }

  // ---------------------------------------------------------------- filters
  function hardFilter(offer, today) {
    const deadline = parseDate(get(offer, "dates.deadline"));
    if (deadline && deadline < today) return `deadline passed (${get(offer, "dates.deadline")})`;
    const enrollBy = parseDate(get(offer, "dates.enroll_by"));
    if (enrollBy && enrollBy < today) return `enrollment closed (${get(offer, "dates.enroll_by")})`;
    const amount = get(offer, "bonus.amount", 0);
    if (amount < config.minBonus) return `${money(amount)} is below your ${money(config.minBonus)} threshold`;
    const inc = get(offer, "eligibility.states_included");
    const st = (config.state || "").toUpperCase();
    if (inc && st && !inc.includes(st)) return `not offered in ${st} (only ${inc.join(", ")})`;
    if ((get(offer, "eligibility.states_excluded") || []).includes(st)) return `excluded in ${st}`;
    if (get(offer, "account.personal_or_business") === "business" && !config.business)
      return "business account (enable it in the sidebar)";
    if (config.chex && get(offer, "bank.chexsystems") === true) return "pulls ChexSystems";
    if (config.highOnly && get(offer, "provenance.confidence") !== "high") return "not high-confidence";
    const need = minCapital(offer);
    if (need && config.capital != null && need > config.capital)
      return `cheapest path needs ${money(need)} parked, you have ${money(config.capital)}`;
    return null;
  }

  function feasibility(offer, openDate, pays, ddAmount) {
    const dd = get(offer, "requirements.direct_deposit", {}) || {};
    const caveats = [];
    const deadline = parseDate(get(offer, "dates.deadline"));
    const enrollBy = parseDate(get(offer, "dates.enroll_by"));
    const openBy = [deadline, enrollBy].filter(Boolean).sort((a, b) => a - b)[0] || null;
    if (openBy && openDate > openBy) return { ok: false, reason: `cannot open by ${isoDate(openBy)}` };

    if (!dd.required) {
      const mb = get(offer, "requirements.min_balance") || {};
      const fund = mb.fund_within_days || 30;
      if (mb.amount) caveats.push(`park ${money(mb.amount)} within ${fund} days` + (mb.hold_days ? ` and hold ${mb.hold_days} days` : ""));
      else caveats.push("no direct deposit required");
      return { ok: true, windowStart: openDate, windowEnd: addDays(openDate, fund), ddDates: [], ddEach: 0, required: 0, completion: addDays(openDate, fund), caveats };
    }
    if (!dd.window_days) return { ok: false, reason: "direct deposit required but the window length is unknown" };

    const earliest = addDays(openDate, ACH_SETTLE_DAYS);
    let start = openDate;
    if (dd.window_starts === "first_deposit") {
      const first = pays.find(d => d >= earliest);
      if (!first) return { ok: false, reason: "no pay date lands after this open date" };
      start = first;
    } else if (!dd.window_starts) {
      caveats.push("window start is unstated — assuming it runs from account opening");
    }
    const end = addDays(start, dd.window_days);
    if (dd.min_amount_each && ddAmount < dd.min_amount_each)
      return { ok: false, reason: `each deposit must be ${money(dd.min_amount_each)}; you have ${money(ddAmount)} per pay date` };

    let required = dd.count || 0;
    if (dd.min_amount_cumulative) {
      if (ddAmount <= 0) return { ok: false, reason: "no direct deposit amount available" };
      required = Math.max(required, Math.ceil(dd.min_amount_cumulative / ddAmount));
    }
    required = Math.max(required, 1);
    const inWindow = pays.filter(d => d >= start && d <= end && d >= earliest);
    if (inWindow.length < required)
      return { ok: false, reason: `needs ${required} deposit(s) of ${money(ddAmount)} inside a ${dd.window_days}-day window; your ${config.cadence} schedule gives ${inWindow.length}` };

    const ddDates = inWindow.slice(0, required);
    if (dd.min_amount_cumulative && required > (dd.count || 0))
      caveats.push(`${money(dd.min_amount_cumulative)} cumulative needs ${required} deposit${required === 1 ? "" : "s"} at ${money(ddAmount)} each`);
    const debit = get(offer, "requirements.debit_transactions");
    if (debit && debit.count) {
      const w = debit.window_days || dd.window_days;
      caveats.push(`${debit.count} debit purchases in ${w} days (~1 every ${Math.round(w / debit.count)} days)`);
    }
    const mb = get(offer, "requirements.min_balance");
    if (mb && mb.amount) caveats.push(`also park ${money(mb.amount)}`);
    if (get(offer, "requirements.enrollment_required")) caveats.push("must enroll in the offer separately");
    if (get(offer, "eligibility.in_branch_required")) caveats.push("branch visit required");
    const fee = get(offer, "account.monthly_fee");
    if (fee) caveats.push(`${money(fee)}/mo fee — ${get(offer, "account.fee_waiver") || "no waiver stated"}`);

    return { ok: true, windowStart: start, windowEnd: end, ddDates, ddEach: ddAmount, required, completion: ddDates[ddDates.length - 1], caveats };
  }

  function score(offer, feas, openDate) {
    const amount = get(offer, "bonus.amount", 0);
    const days = Math.max(Math.round((feas.completion - openDate) / DAY), 1);
    const mb = get(offer, "requirements.min_balance") || {};
    const parked = mb.amount ? mb.amount * APY * (((mb.hold_days || 90) + (mb.fund_within_days || 0)) / 365) : 0;
    const efficiency = (amount - parked) / (days / 7);
    let penalty = 0; const reasons = [];
    if (get(offer, "eligibility.pull_type") === "hard") { penalty += 15; reasons.push("hard credit pull"); }
    const chex = get(offer, "bank.chexsystems");
    if (chex === true) { penalty += 8; reasons.push("pulls ChexSystems"); }
    else if (chex === null) { penalty += 3; reasons.push("ChexSystems behaviour unknown"); }
    const conf = get(offer, "provenance.confidence");
    if (conf === "low") { penalty += 12; reasons.push("low-confidence data"); }
    else if (conf === "medium") { penalty += 4; reasons.push("aggregator-sourced data"); }
    if (!get(offer, "requirements.direct_deposit.window_starts") && get(offer, "requirements.direct_deposit.required")) {
      penalty += 6; reasons.push("window start semantics unknown");
    }
    return { score: efficiency - penalty, efficiency, penalty, reasons, days, parked, net: amount - parked };
  }

  // ---------------------------------------------------------------- plan
  function buildPlan() {
    const today = new Date(new Date().toISOString().slice(0, 10) + "T00:00:00Z");
    const horizon = addDays(today, 365);
    let next = parseDate(config.nextPay) || addDays(today, 7);
    if (next < today) {
      const step = { weekly: 7, biweekly: 14 }[config.cadence];
      if (step) next = addDays(next, step * Math.ceil((today - next) / DAY / step));
      else next = addMonths(next, 1);
    }
    const pays = payDates(next, config.cadence, addDays(horizon, 400));
    const rejected = [];
    const candidates = [];

    for (const offer of offers) {
      const reason = hardFilter(offer, today);
      if (reason) rejected.push({ offer, reason, stage: "filter" });
      else candidates.push(offer);
    }

    const dedicated = Number(config.ddAmount) || 0;
    const ranked = [];
    for (const offer of candidates) {
      let best = null, lastReason = "";
      for (const [variant, note] of variants(offer, config.capital)) {
        const feas = feasibility(variant, addDays(today, 1), pays, dedicated);
        if (!feas.ok) { lastReason = lastReason || feas.reason; continue; }
        const s = score(variant, feas, addDays(today, 1));
        if (!best || s.score > best.s.score) best = { variant, s, note };
      }
      if (!best) rejected.push({ offer, reason: lastReason || "no achievable tier", stage: "infeasible" });
      else ranked.push({ offer, ...best });
    }
    ranked.sort((a, b) => b.s.score - a.s.score);

    const maxSplit = config.splittable ? 2 : 1;
    const selected = [];
    for (const entry of ranked) {
      const offer = entry.variant;
      const needsDD = !!get(offer, "requirements.direct_deposit.required");
      const needsCapital = get(offer, "requirements.min_balance.amount", 0) || 0;
      const openBy = [parseDate(get(offer, "dates.deadline")), parseDate(get(offer, "dates.enroll_by"))]
        .filter(Boolean).sort((a, b) => a - b)[0] || horizon;
      let cursor = addDays(today, 1), placed = null, blocker = "deposit timing", lastReason = "";

      while (cursor <= (openBy < horizon ? openBy : horizon)) {
        const active = selected.filter(s => s.openDate <= cursor && cursor < s.bonusPost);
        if (active.length >= config.concurrent) {
          blocker = `your ${config.concurrent}-account concurrency cap`;
          cursor = active.map(s => s.bonusPost).sort((a, b) => a - b)[0] || addDays(cursor, 1);
          continue;
        }
        if (needsCapital && config.capital != null) {
          const committed = selected.filter(s => s.openDate <= cursor && cursor < s.capitalFree)
            .reduce((sum, s) => sum + s.capital, 0);
          if (committed + needsCapital > config.capital) {
            blocker = "cash already committed to higher-ranked offers";
            cursor = addDays(cursor, 7); continue;
          }
        }
        let share = dedicated;
        if (needsDD) {
          const concurrentDD = active.filter(s => s.needsDD);
          if (concurrentDD.length >= maxSplit) {
            blocker = `deposit splitting (at most ${maxSplit} account${maxSplit === 1 ? "" : "s"} per pay cycle)`;
            const nxt = concurrentDD.map(s => s.completion).sort((a, b) => a - b)[0];
            cursor = nxt && nxt > cursor ? addDays(nxt, 1) : addDays(cursor, 1);
            continue;
          }
          if (config.splittable && concurrentDD.length) share = dedicated / (concurrentDD.length + 1);
        }
        const feas = feasibility(offer, cursor, pays, share);
        if (feas.ok) { placed = { cursor, feas, share }; break; }
        lastReason = feas.reason || lastReason;
        cursor = addDays(cursor, 1);
      }

      if (!placed) {
        rejected.push({ offer: entry.offer, stage: "unslotted",
          reason: blocker === "deposit timing" && lastReason ? `no open date fits — ${lastReason}` : `no open date fits — blocked mostly by ${blocker}` });
        continue;
      }
      const { cursor: openDate, feas, share } = placed;
      const payoutDays = get(offer, "bonus.payout_days_after_completion");
      const hold = get(offer, "clawback.min_hold_days_after_bonus");
      const bonusPost = addDays(feas.completion, payoutDays ?? DEFAULT_PAYOUT_DAYS);
      const safeClose = addDays(bonusPost, hold ?? ASSUMED_HOLD_DAYS);
      const mb = get(offer, "requirements.min_balance") || {};
      const caveats = [...feas.caveats];
      if (entry.note) caveats.unshift(entry.note);
      if (payoutDays == null) caveats.push(`payout timing not published — assuming ${DEFAULT_PAYOUT_DAYS} days`);
      if (hold == null) caveats.push(`no hold period published — assuming ${ASSUMED_HOLD_DAYS} days before closing is safe`);
      if (get(offer, "clawback.early_close_fee")) caveats.push(`${money(get(offer, "clawback.early_close_fee"))} early-close fee`);
      if (get(offer, "provenance.confidence") !== "high") caveats.push("verify the terms with the bank before opening");
      if (share < dedicated) caveats.push(`assumes a split paycheck — ${money(share)} to this account`);
      const cooldown = get(offer, "eligibility.cooldown_months");

      selected.push({
        id: offer.id, bank: get(offer, "bank.name"), account: get(offer, "account.name"),
        amount: get(offer, "bonus.amount", 0), headline: get(entry.offer, "bonus.amount", 0),
        promo: get(offer, "eligibility.promo_code"), url: get(offer, "provenance.source_url"),
        confidence: get(offer, "provenance.confidence"),
        openDate, openBy, windowStart: feas.windowStart, windowEnd: feas.windowEnd,
        ddDates: feas.ddDates, ddEach: feas.ddEach, required: feas.required, needsDD,
        completion: feas.completion, bonusPost, safeClose,
        cooldownEnds: cooldown ? addMonths(bonusPost, cooldown) : null,
        capital: needsCapital,
        capitalFree: needsCapital ? addDays(openDate, (mb.fund_within_days || 0) + (mb.hold_days || 0)) : openDate,
        caveats, ...score(offer, feas, openDate),
      });
    }
    selected.sort((a, b) => a.openDate - b.openDate);
    return { selected, rejected, today };
  }

  // ---------------------------------------------------------------- render
  function renderStats(plan) {
    const gross = plan.selected.reduce((s, i) => s + i.amount, 0);
    const weeks = Math.max((Math.max(...plan.selected.map(i => i.bonusPost), plan.today) - plan.today) / DAY / 7, 1);
    document.getElementById("stats").innerHTML = [
      ["Offers tracked", offers.length],
      ["In your plan", plan.selected.length],
      ["Gross bonus", money(gross)],
      ["Per week", money(plan.selected.length ? gross / weeks : 0)],
    ].map(([l, n]) => `<div class="stat"><span class="n">${esc(n)}</span><span class="l">${esc(l)}</span></div>`).join("");
  }

  function renderPlan(plan) {
    if (!plan.selected.length)
      return `<p class="empty">Nothing qualified. Check the <strong>Why not</strong> tab — usually it's the state filter or the minimum bonus.</p>`;
    return plan.selected.map((i, n) => `
      <div class="card">
        <span class="amount">${money(i.amount)}${i.headline !== i.amount ? `<span class="meta" style="font-size:.7rem"> of ${money(i.headline)}</span>` : ""}</span>
        <h3>${n + 1}. ${esc(i.bank)} — ${esc(i.account)}</h3>
        <div class="meta">
          <span class="tag ${esc(i.confidence)}">${esc(i.confidence)}</span>
          ${money(i.efficiency)}/week · score ${Math.round(i.score)}${i.reasons.length ? ` · ${esc(i.reasons.join(", "))}` : ""}
        </div>
        <ol class="steps">
          <li>Open <strong>${isoDate(i.openDate)}</strong> (offer ends ${isoDate(i.openBy)})${i.promo ? ` — promo code <code>${esc(i.promo)}</code>` : ""}</li>
          ${i.needsDD ? i.ddDates.map((d, k) => `<li>DD #${k + 1} of ${money(i.ddEach)} by <strong>${isoDate(d)}</strong></li>`).join("") : ""}
          ${i.capital ? `<li>Park ${money(i.capital)} until ${isoDate(i.capitalFree)}</li>` : ""}
          <li>Bonus expected <strong>${isoDate(i.bonusPost)}</strong></li>
          <li>Safe to close <strong>${isoDate(i.safeClose)}</strong>${i.cooldownEnds ? ` · re-eligible ${isoDate(i.cooldownEnds)}` : ""}</li>
        </ol>
        ${i.caveats.map(c => `<div class="caveat">! ${esc(c)}</div>`).join("")}
        <div class="meta" style="margin-top:8px"><a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">source</a></div>
      </div>`).join("");
  }

  function renderAll() {
    const rows = [...offers].sort((a, b) => get(b, "bonus.amount", 0) - get(a, "bonus.amount", 0)).map(o => {
      const dd = get(o, "requirements.direct_deposit", {}) || {};
      let req = "—";
      if (dd.required) {
        req = dd.min_amount_cumulative ? `${money(dd.min_amount_cumulative)} total`
          : dd.min_amount_each ? `${dd.count || 1} x ${money(dd.min_amount_each)}` : "DD required";
        if (dd.window_days) req += ` / ${dd.window_days}d`;
      } else if (get(o, "requirements.min_balance.amount")) {
        req = `${money(get(o, "requirements.min_balance.amount"))} balance`;
      }
      const inc = get(o, "eligibility.states_included");
      return `<tr>
        <td>${esc(get(o, "bank.name"))}<div class="meta">${esc(get(o, "account.name"))}</div></td>
        <td class="num">${money(get(o, "bonus.amount", 0))}</td>
        <td>${esc(req)}</td>
        <td>${inc ? esc(inc.join(" ")) : "nationwide"}</td>
        <td>${esc(get(o, "dates.deadline") || "no end date")}</td>
        <td><span class="tag ${esc(get(o, "provenance.confidence"))}">${esc(get(o, "provenance.confidence"))}</span><div class="meta">${esc(get(o, "provenance.last_verified"))}</div></td>
      </tr>`;
    }).join("");
    return `<div class="panel scroll"><table>
      <thead><tr><th>Bank / account</th><th class="num">Bonus</th><th>Requirement</th><th>States</th><th>Deadline</th><th>Confidence</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  function renderWhy(plan) {
    const labels = { unslotted: "No room in the calendar", infeasible: "Infeasible on your pay schedule", filter: "Filtered out" };
    return ["unslotted", "infeasible", "filter"].map(stage => {
      const items = plan.rejected.filter(r => r.stage === stage);
      if (!items.length) return "";
      const rows = items.sort((a, b) => get(b.offer, "bonus.amount", 0) - get(a.offer, "bonus.amount", 0))
        .map(r => `<tr><td>${esc(get(r.offer, "bank.name"))} ${esc(get(r.offer, "account.name"))}</td>
          <td class="num">${money(get(r.offer, "bonus.amount", 0))}</td><td>${esc(r.reason)}</td></tr>`).join("");
      return `<div class="panel scroll" style="margin-bottom:14px">
        <h2>${labels[stage]} (${items.length})</h2>
        <table><thead><tr><th>Account</th><th class="num">Bonus</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }).join("") || `<p class="empty">Nothing was rejected.</p>`;
  }

  function render() {
    const plan = buildPlan();
    renderStats(plan);
    const target = document.getElementById("view");
    target.innerHTML = view === "plan" ? renderPlan(plan) : view === "all" ? renderAll() : renderWhy(plan);
  }

  // ---------------------------------------------------------------- wiring
  function bind() {
    const map = {
      state: "state", minBonus: "minBonus", concurrent: "concurrent", capital: "capital",
      cadence: "cadence", nextPay: "nextPay", ddAmount: "ddAmount",
    };
    for (const [id, key] of Object.entries(map)) {
      const el = document.getElementById(id);
      el.value = config[key];
      el.addEventListener("change", () => {
        config[key] = el.type === "number" ? Number(el.value) : el.value.toUpperCase?.() && id === "state" ? el.value.toUpperCase() : el.value;
        save(); render();
      });
    }
    for (const id of ["splittable", "business", "chex", "highOnly"]) {
      const el = document.getElementById(id);
      el.checked = !!config[id];
      el.addEventListener("change", () => { config[id] = el.checked; save(); render(); });
    }
    document.querySelectorAll(".tab").forEach(tab => {
      tab.addEventListener("click", () => {
        view = tab.dataset.view;
        document.querySelectorAll(".tab").forEach(t => t.setAttribute("aria-selected", String(t === tab)));
        render();
      });
    });
  }

  async function boot() {
    for (const url of [...LOCAL, CDN]) {
      try {
        const resp = await fetch(url, { cache: "no-cache" });
        if (!resp.ok) continue;
        const data = await resp.json();
        offers = (data.offers || data).filter(o => !o.no_offer);
        bind();
        render();
        return;
      } catch (_) { /* try the next source */ }
    }
    document.getElementById("view").innerHTML =
      `<p class="empty">Couldn't load offer data. Run <code>python -m scripts.build_dist</code>, or open this page over HTTP rather than from the filesystem.</p>`;
  }

  boot();
})();
