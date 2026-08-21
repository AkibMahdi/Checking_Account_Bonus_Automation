<script>
(() => {
"use strict";
const OFFERS = __OFFERS_JSON__.filter(o => !o.no_offer);
const BUILT = "__BUILT_ON__";

const KEY = "bonusLadder.v1", DAY = 864e5;
const ACH = 1, PAYOUT_FALLBACK = 60, HOLD_FALLBACK = 180, APY = 0.04;
const DEFAULTS = {
  state:"NY", minBonus:150, concurrent:3, capital:20000,
  cadence:"biweekly", nextPay:"", ddAmount:2400,
  skipBanks:"", splittable:true, business:false, chex:false, highOnly:false, totalMode:false,
};
let cfg, view = "plan", lastPlan = null;   // cfg is assigned after the helpers below

/* ---------------------------------------------------------------- helpers */
const iso = d => new Date(d).toISOString().slice(0,10);
const parse = s => s ? new Date(s + "T00:00:00Z") : null;
const addD = (d,n) => new Date(d.getTime() + n*DAY);
function addM(d,n){ const o=new Date(d.getTime()), day=o.getUTCDate();
  o.setUTCMonth(o.getUTCMonth()+n); if(o.getUTCDate()<day) o.setUTCDate(0); return o; }
const money = n => "$" + Math.round(n).toLocaleString("en-US");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const g = (o,p,f=null) => p.split(".").reduce((a,k)=> (a && a[k]!=null ? a[k] : null), o) ?? f;
const normBank = s => String(s||"").toLowerCase()
  .replace(/\b(bank|n\.a\.|na|inc|corp|the|federal|credit union|fcu)\b/g," ")
  .replace(/[^a-z0-9]+/g,"");
const pretty = d => new Date(d).toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric",timeZone:"UTC"});
const short = d => new Date(d).toLocaleDateString("en-US",{month:"short",day:"numeric",timeZone:"UTC"});

function loadCfg(){
  const base = {...DEFAULTS};
  if(!base.nextPay){ const t=new Date(); base.nextPay = iso(addD(t, 7)); }
  try{ const raw = localStorage.getItem(KEY); return raw ? {...base, ...JSON.parse(raw)} : base; }
  catch(_){ return base; }
}
function saveCfg(){ try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(_){} }

/* ---------------------------------------------------------------- pay dates */
function payDates(start, cadence, end){
  const out=[]; let c=new Date(start.getTime());
  if(cadence==="weekly"||cadence==="biweekly"){
    const step = cadence==="weekly"?7:14;
    while(c<=end){ out.push(new Date(c.getTime())); c=addD(c,step); }
  } else if(cadence==="monthly"){
    let n=0; while(c<=end){ out.push(new Date(c.getTime())); c=addM(start,++n); }
  } else {
    const first = start.getUTCDate()<=15 ? start.getUTCDate() : start.getUTCDate()-15;
    let m = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    while(m<=end){
      const last = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth()+1, 0)).getUTCDate();
      for(const day of [first, first+15]){
        const d = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth(), Math.min(day,last)));
        if(d>=start && d<=end) out.push(d);
      }
      m = addM(m,1);
    }
    out.sort((a,b)=>a-b);
  }
  return out;
}

/* ---------------------------------------------------------------- tiers */
function minCapital(o){
  const base = g(o,"requirements.min_balance.amount",0) || 0;
  const t = g(o,"bonus.tiers");
  if(!t || g(o,"bonus.tier_mode")!=="alternative") return base;
  return Math.min(...t.map(x => x.min_balance ?? base));
}
function variants(o, capital){
  const tiers = g(o,"bonus.tiers"), mode = g(o,"bonus.tier_mode");
  if(!tiers) return [[o,null]];
  const clone = () => JSON.parse(JSON.stringify(o));
  if(mode==="additive"){
    const first = tiers.reduce((a,b)=> (a.window_days ?? 1e9) <= (b.window_days ?? 1e9) ? a : b);
    const v = clone(); v.bonus.amount = first.amount;
    const dd = v.requirements.direct_deposit;
    if(first.dd_cumulative!=null) dd.min_amount_cumulative = first.dd_cumulative;
    if(first.dd_count!=null) dd.count = first.dd_count;
    if(first.window_days) dd.window_days = first.window_days;
    const later = tiers.filter(t=>t!==first).reduce((s,t)=>s+t.amount,0);
    return [[v, `${money(first.amount)} lands near-term; the remaining ${money(later)} only arrives if you keep the account active through every anniversary`]];
  }
  if(mode==="repeating"){
    const t = tiers[0], cycles = t.repeatable_cycles || 1;
    const v = clone(); v.bonus.amount = t.amount * cycles;
    v.requirements.direct_deposit.window_days = Math.min((t.window_days||30)*cycles, 730);
    return [[v, `${money(t.amount)} per statement cycle for ${cycles} cycles — a ${Math.round((t.window_days||30)*cycles/30)}-month commitment, not a one-off`]];
  }
  const out = [];
  for(const t of [...tiers].sort((a,b)=>b.amount-a.amount)){
    if(t.amount<=0) continue;
    const need = t.min_balance ?? (g(o,"requirements.min_balance.amount",0)||0);
    if(need && capital!=null && need>capital) continue;
    const v = clone(); v.bonus.amount = t.amount;
    const dd = v.requirements.direct_deposit;
    if(t.dd_cumulative!=null){ dd.min_amount_cumulative = t.dd_cumulative; dd.required = true; }
    if(t.dd_count!=null) dd.count = t.dd_count;
    if(t.window_days) dd.window_days = t.window_days;
    if(need){
      v.requirements.min_balance = Object.assign({fund_within_days:t.window_days, hold_days:null},
        v.requirements.min_balance||{}, {amount:need});
    } else if(t.dd_cumulative!=null){ v.requirements.min_balance = null; }
    const note = t.amount !== g(o,"bonus.amount")
      ? `planned at the ${money(t.amount)} tier rather than the ${money(g(o,"bonus.amount"))} headline — ${t.condition}` : null;
    out.push([v,note]);
  }
  return out.length ? out : [[o,null]];
}

/* ---------------------------------------------------------------- filters */
function hardFilter(o, today){
  const dl = parse(g(o,"dates.deadline"));
  if(dl && dl < today) return `the offer closed on ${pretty(dl)}`;
  const eb = parse(g(o,"dates.enroll_by"));
  if(eb && eb < today) return `enrollment closed on ${pretty(eb)}`;
  const amt = g(o,"bonus.amount",0);
  if(amt < cfg.minBonus) return `${money(amt)} is under your ${money(cfg.minBonus)} floor`;
  const st = (cfg.state||"").toUpperCase();
  const inc = g(o,"eligibility.states_included");
  if(inc && st && !inc.includes(st)) return `not offered in ${st} — only ${inc.join(", ")}`;
  if((g(o,"eligibility.states_excluded")||[]).includes(st)) return `explicitly excluded in ${st}`;
  if(g(o,"account.personal_or_business")==="business" && !cfg.business)
    return "a business account — turn those on in the sidebar to include it";
  if(cfg.chex && g(o,"bank.chexsystems")===true) return "this bank pulls ChexSystems";
  const skips = String(cfg.skipBanks||"").split(",").map(normBank).filter(Boolean);
  const bank = g(o,"bank.name","");
  if(skips.some(x => normBank(bank)===x || normBank(bank).includes(x)))
    return `you told me to skip ${bank}`;
  if(cfg.highOnly && g(o,"provenance.confidence")!=="high") return "the terms aren't confirmed on the bank's own site";
  const need = minCapital(o);
  if(need && cfg.capital!=null && need>cfg.capital)
    return `even the cheapest path needs ${money(need)} parked; you set ${money(cfg.capital)}`;
  return null;
}

function feasibility(o, open, pays, ddAmt){
  const dd = g(o,"requirements.direct_deposit",{}) || {};
  const caveats = [];
  const openBy = [parse(g(o,"dates.deadline")), parse(g(o,"dates.enroll_by"))].filter(Boolean).sort((a,b)=>a-b)[0] || null;
  if(openBy && open > openBy) return {ok:false, reason:`it has to be open by ${pretty(openBy)}`};

  if(!dd.required){
    const mb = g(o,"requirements.min_balance") || {};
    const fund = mb.fund_within_days || 30;
    caveats.push(mb.amount
      ? `park ${money(mb.amount)} within ${fund} days${mb.hold_days?` and leave it there ${mb.hold_days} days`:""}`
      : "no direct deposit required");
    const done = addD(open, fund);
    return {ok:true, windowStart:open, windowEnd:done, ddDates:[], ddEach:0, required:0, completion:done, caveats};
  }
  if(!dd.window_days) return {ok:false, reason:"the bank never states how long you have to meet the requirement"};

  const earliest = addD(open, ACH);
  let start = open;
  if(dd.window_starts === "first_deposit"){
    const first = pays.find(d => d >= earliest);
    if(!first) return {ok:false, reason:"no payday lands after that opening date"};
    start = first;
  } else if(!dd.window_starts){
    caveats.push("the bank doesn't say when the clock starts — this assumes it runs from the day you open");
  }
  const end = addD(start, dd.window_days);
  if(dd.min_amount_each && ddAmt < dd.min_amount_each)
    return {ok:false, reason:`each deposit has to be ${money(dd.min_amount_each)}; you'd have ${money(ddAmt)} per payday`};

  let required = dd.count || 0;
  if(dd.min_amount_cumulative){
    if(ddAmt<=0) return {ok:false, reason:"no direct deposit amount to work with"};
    required = Math.max(required, Math.ceil(dd.min_amount_cumulative/ddAmt));
  }
  required = Math.max(required,1);
  const inWin = pays.filter(d => d>=start && d<=end && d>=earliest);
  if(inWin.length < required)
    return {ok:false, reason:`it wants ${required} deposits of ${money(ddAmt)} inside ${dd.window_days} days; your schedule gives ${inWin.length}`};

  const ddDates = inWin.slice(0,required);
  if(dd.min_amount_cumulative && required > (dd.count||0))
    caveats.push(`${money(dd.min_amount_cumulative)} total means ${required} deposit${required===1?"":"s"} at ${money(ddAmt)} each`);
  const debit = g(o,"requirements.debit_transactions");
  if(debit && debit.count){
    const w = debit.window_days || dd.window_days;
    caveats.push(`${debit.count} debit card purchases in ${w} days — roughly one every ${Math.round(w/debit.count)} days`);
  }
  const mb = g(o,"requirements.min_balance");
  if(mb && mb.amount) caveats.push(`also park ${money(mb.amount)}`);
  if(g(o,"requirements.enrollment_required")) caveats.push("you have to enroll in the offer separately from opening the account");
  if(g(o,"eligibility.in_branch_required")) caveats.push("requires a branch visit");
  const fee = g(o,"account.monthly_fee");
  if(fee) caveats.push(`${money(fee)}/month fee — ${g(o,"account.fee_waiver") || "no waiver stated"}`);
  const qn = g(o,"requirements.direct_deposit.qualifying_notes");
  if(qn) caveats.push(qn);

  return {ok:true, windowStart:start, windowEnd:end, ddDates, ddEach:ddAmt, required, completion:ddDates[ddDates.length-1], caveats};
}

function score(o, feas, open){
  const amt = g(o,"bonus.amount",0);
  const days = Math.max(Math.round((feas.completion-open)/DAY),1);
  const mb = g(o,"requirements.min_balance") || {};
  const parked = mb.amount ? mb.amount*APY*(((mb.hold_days||90)+(mb.fund_within_days||0))/365) : 0;
  const eff = (amt-parked)/(days/7);
  let pen=0; const why=[];
  if(g(o,"eligibility.pull_type")==="hard"){ pen+=15; why.push("hard credit pull"); }
  const chex = g(o,"bank.chexsystems");
  if(chex===true){ pen+=8; why.push("pulls ChexSystems"); }
  else if(chex===null){ pen+=3; why.push("ChexSystems behaviour unknown"); }
  const conf = g(o,"provenance.confidence");
  if(conf==="low"){ pen+=12; why.push("unverified terms"); }
  else if(conf==="medium"){ pen+=4; why.push("terms from an aggregator"); }
  if(!g(o,"requirements.direct_deposit.window_starts") && g(o,"requirements.direct_deposit.required")){
    pen+=6; why.push("unclear window start");
  }
  return {score:eff-pen, efficiency:eff, penalty:pen, reasons:why, days, parked, net:amt-parked};
}

/* ---------------------------------------------------------------- plan */
function buildPlan(){
  const today = new Date(new Date().toISOString().slice(0,10)+"T00:00:00Z");
  const horizon = addD(today, 365);
  let next = parse(cfg.nextPay) || addD(today,7);
  if(next < today){
    const step = {weekly:7, biweekly:14}[cfg.cadence];
    next = step ? addD(next, step*Math.ceil((today-next)/DAY/step)) : addM(next,1);
  }
  const pays = payDates(next, cfg.cadence, addD(horizon,400));
  const rejected = [], candidates = [];
  for(const o of OFFERS){
    const r = hardFilter(o, today);
    if(r) rejected.push({offer:o, reason:r, stage:"filter"}); else candidates.push(o);
  }

  const dedicated = Number(cfg.ddAmount) || 0;
  const ranked = [];
  for(const o of candidates){
    let best=null, lastReason="", floorReason="";
    for(const [v,note] of variants(o, cfg.capital)){
      const reachable = g(v,"bonus.amount",0);
      if(reachable < cfg.minBonus){
        floorReason = floorReason || `the ${money(reachable)} tier is under your ${money(cfg.minBonus)} floor`;
        continue;
      }
      const f = feasibility(v, addD(today,1), pays, dedicated);
      if(!f.ok){ lastReason = lastReason || f.reason; continue; }
      const s = score(v, f, addD(today,1));
      const key = cfg.totalMode ? s.net : s.score;
      if(!best || key > best.key) best = {v, s, note, key};
    }
    if(!best) rejected.push({offer:o, stage:"infeasible",
      reason: [lastReason, floorReason].filter(Boolean).join("; and ") || "no tier you can reach"});
    else ranked.push({offer:o, ...best});
  }
  ranked.sort((a,b)=> (cfg.totalMode ? b.s.net-a.s.net : b.s.score-a.s.score));

  const maxSplit = cfg.splittable ? 2 : 1;
  const picked = [];
  for(const e of ranked){
    const o = e.v;
    const needsDD = !!g(o,"requirements.direct_deposit.required");
    const needsCap = g(o,"requirements.min_balance.amount",0) || 0;
    const openBy = [parse(g(o,"dates.deadline")), parse(g(o,"dates.enroll_by"))].filter(Boolean).sort((a,b)=>a-b)[0] || horizon;
    const limit = openBy < horizon ? openBy : horizon;
    let cur = addD(today,1), placed=null, blocker="deposit timing", lastReason="";
    while(cur <= limit){
      const active = picked.filter(s => s.openDate<=cur && cur<s.bonusPost);
      if(active.length >= cfg.concurrent){
        blocker = `your limit of ${cfg.concurrent} account${cfg.concurrent===1?"":"s"} at a time`;
        cur = active.map(s=>s.bonusPost).sort((a,b)=>a-b)[0] || addD(cur,1); continue;
      }
      if(needsCap && cfg.capital!=null){
        const used = picked.filter(s=>s.openDate<=cur && cur<s.capitalFree).reduce((n,s)=>n+s.capital,0);
        if(used+needsCap > cfg.capital){ blocker="cash already committed to better-paying offers"; cur=addD(cur,7); continue; }
      }
      let share = dedicated;
      if(needsDD){
        const dds = active.filter(s=>s.needsDD);
        if(dds.length >= maxSplit){
          blocker = `only ${maxSplit} account${maxSplit===1?"":"s"} can be fed per pay cycle`;
          const nx = dds.map(s=>s.completion).sort((a,b)=>a-b)[0];
          cur = nx && nx>cur ? addD(nx,1) : addD(cur,1); continue;
        }
        if(cfg.splittable && dds.length) share = dedicated/(dds.length+1);
      }
      const f = feasibility(o, cur, pays, share);
      if(f.ok){ placed = {cur,f,share}; break; }
      lastReason = f.reason || lastReason; cur = addD(cur,1);
    }
    if(!placed){
      rejected.push({offer:e.offer, stage:"unslotted",
        reason: blocker==="deposit timing" && lastReason ? lastReason : `no opening date fits — held up by ${blocker}`});
      continue;
    }
    const {cur:openDate, f, share} = placed;
    const payoutDays = g(o,"bonus.payout_days_after_completion");
    const hold = g(o,"clawback.min_hold_days_after_bonus");
    const bonusPost = addD(f.completion, payoutDays ?? PAYOUT_FALLBACK);
    const safeClose = addD(bonusPost, hold ?? HOLD_FALLBACK);
    const mb = g(o,"requirements.min_balance") || {};
    const caveats = [...f.caveats];
    if(e.note) caveats.unshift(e.note);
    if(payoutDays==null) caveats.push(`the bank doesn't publish payout timing — this assumes ${PAYOUT_FALLBACK} days`);
    if(hold==null) caveats.push(`no hold period published — assume ${HOLD_FALLBACK} days before closing is safe`);
    if(g(o,"clawback.early_close_fee")) caveats.push(`${money(g(o,"clawback.early_close_fee"))} fee if you close early`);
    if(g(o,"provenance.confidence")!=="high") caveats.push("confirm these terms with the bank before you open");
    if(share < dedicated) caveats.push(`assumes a split paycheck — ${money(share)} of it goes here`);
    const cd = g(o,"eligibility.cooldown_months");
    picked.push({
      id:o.id, bank:g(o,"bank.name"), account:g(o,"account.name"),
      amount:g(o,"bonus.amount",0), headline:g(e.offer,"bonus.amount",0),
      promo:g(o,"eligibility.promo_code"), url:g(o,"provenance.source_url"),
      confidence:g(o,"provenance.confidence"), verified:g(o,"provenance.last_verified"),
      openDate, openBy, windowStart:f.windowStart, windowEnd:f.windowEnd,
      ddDates:f.ddDates, ddEach:f.ddEach, required:f.required, needsDD,
      completion:f.completion, bonusPost, safeClose,
      cooldownEnds: cd ? addM(bonusPost, cd) : null,
      capital:needsCap,
      capitalFree: needsCap ? addD(openDate,(mb.fund_within_days||0)+(mb.hold_days||0)) : openDate,
      caveats, ...score(o,f,openDate),
    });
  }
  picked.sort((a,b)=>a.openDate-b.openDate);
  return {selected:picked, rejected, today, horizon};
}

/* ---------------------------------------------------------------- render */
function renderFigures(p){
  const gross = p.selected.reduce((s,i)=>s+i.amount,0);
  const last = p.selected.length ? new Date(Math.max(...p.selected.map(i=>+i.bonusPost))) : p.today;
  const weeks = Math.max((last-p.today)/DAY/7, 1);
  const soonest = p.selected[0];
  const tiles = [
    {v:money(gross), k:"you'd bank", n: p.selected.length ? `by ${pretty(last)}` : "nothing qualifies yet", lead:true},
    {v:String(p.selected.length), k:"accounts to open", n:`out of ${OFFERS.length} tracked`},
    {v:money(p.selected.length ? gross/weeks : 0), k:"per week", n:`over ${Math.round(weeks)} weeks`},
    {v: soonest ? short(soonest.openDate) : "—", k:"first move", n: soonest ? `open ${soonest.bank}` : "adjust your filters"},
  ];
  document.getElementById("figures").innerHTML = tiles.map(t =>
    `<div class="fig${t.lead?" lead":""}"><span class="v">${esc(t.v)}</span><span class="k">${esc(t.k)}</span><span class="n">${esc(t.n)}</span></div>`).join("");
  document.getElementById("cPlan").textContent = p.selected.length;
  document.getElementById("cAll").textContent = OFFERS.length;
  document.getElementById("cWhy").textContent = p.rejected.length;
}

function renderSchedule(p){
  if(!p.selected.length) return "";
  const start = p.today;
  const end = new Date(Math.max(...p.selected.map(i=>+i.safeClose)));
  const span = Math.max(end-start, DAY);
  const pct = d => ((new Date(d)-start)/span)*100;
  const months = [];
  let m = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  while(m <= end){
    if(m >= start) months.push(m);
    m = addM(m,1);
  }
  const axis = months.map(d =>
    `<span style="left:${pct(d).toFixed(3)}%">${new Date(d).toLocaleDateString("en-US",{month:"short",timeZone:"UTC"})}${d.getUTCMonth()===0?" "+d.getUTCFullYear():""}</span>`).join("");

  const lanes = p.selected.map(i => {
    const a = pct(i.openDate), b = pct(i.completion), c = pct(i.bonusPost), d = pct(i.safeClose);
    const ticks = i.ddDates.map(t =>
      `<i class="tick" style="left:${pct(t).toFixed(3)}%" title="Deposit due ${pretty(t)}"></i>`).join("");
    return `<div class="lane">
      <div class="who"><b>${esc(i.bank)}</b> ${esc(i.account)}</div>
      <div class="track">
        <div class="seg hold" style="left:${c.toFixed(3)}%;width:${Math.max(d-c,0.4).toFixed(3)}%" title="Hold period — closing before ${pretty(i.safeClose)} risks a clawback"></div>
        <div class="seg wait" style="left:${b.toFixed(3)}%;width:${Math.max(c-b,0.4).toFixed(3)}%" title="Waiting for the bonus to post"></div>
        <div class="seg win" style="left:${a.toFixed(3)}%;width:${Math.max(b-a,0.6).toFixed(3)}%" title="Requirement window"></div>
        ${ticks}
        <div class="pin" style="left:${c.toFixed(3)}%">${esc(money(i.amount))}</div>
      </div></div>`;
  }).join("");

  return `<section class="schedule" style="--lane:190px">
    <div class="sched-scroll"><div class="sched-inner">
      <div class="axis">${axis}</div>${lanes}
    </div></div>
    <div class="legend">
      <span><i class="chip win"></i>meeting the requirement</span>
      <span><i class="chip wait"></i>waiting for the bonus</span>
      <span><i class="chip hold"></i>hold it open or lose the bonus</span>
      <span style="font-family:var(--mono);font-size:.72rem">| = a deposit has to land</span>
    </div></section>`;
}

function renderNext(p){
  const soon = [];
  for(const i of p.selected){
    soon.push({when:i.openDate, what:`Open <b>${esc(i.bank)} ${esc(i.account)}</b>${i.promo?` with code <span class="tag code">${esc(i.promo)}</span>`:""}`});
    i.ddDates.forEach((d,n)=> soon.push({when:d, what:`Deposit #${n+1} of ${money(i.ddEach)} must land at ${esc(i.bank)}`}));
    if(i.capital) soon.push({when:i.openDate, what:`Move ${money(i.capital)} into ${esc(i.bank)}`});
  }
  const cutoff = addD(p.today, 45);
  const list = soon.filter(s => s.when <= cutoff).sort((a,b)=>a.when-b.when).slice(0,7);
  if(!list.length) return "";
  return `<section class="next"><h3>Next 45 days</h3><ul>${list.map(s=>{
    const days = Math.round((s.when-p.today)/DAY);
    return `<li><time>${esc(short(s.when))}</time><span>${s.what}</span>
      <span class="in">${days<=0?"today":`in ${days}d`}</span></li>`;}).join("")}</ul></section>`;
}

function renderLadder(p){
  if(!p.selected.length) return `<div class="sheet"><div class="empty"><b>Nothing clears your filters.</b>
    Check the “Why not” tab — it's usually the state, the minimum bonus, or the size of your paycheck.</div></div>`;
  const cards = p.selected.map((i,n) => {
    const steps = [];
    steps.push([short(i.openDate), `Open the account${i.promo?` — code <span class="tag code">${esc(i.promo)}</span>`:""}
      <span class="fine">offer closes ${pretty(i.openBy)}</span>`]);
    if(i.needsDD) i.ddDates.forEach((d,k)=> steps.push([short(d),
      `Deposit #${k+1} of <b>${money(i.ddEach)}</b> must land`]));
    if(i.capital) steps.push([short(i.openDate), `Park ${money(i.capital)} <span class="fine">free again ${pretty(i.capitalFree)}</span>`]);
    steps.push([short(i.bonusPost), `Bonus should post <span class="fine">chase the bank if it hasn't</span>`]);
    steps.push([short(i.safeClose), `Safe to close${i.cooldownEnds?` <span class="fine">eligible again ${pretty(i.cooldownEnds)}</span>`:""}`]);
    return `<article class="card">
      <div class="card-top">
        <span class="rung">${String(n+1).padStart(2,"0")}</span>
        <div><h3>${esc(i.bank)} — ${esc(i.account)}</h3>
          <div class="sub"><span class="tag ${esc(i.confidence)}">${esc(i.confidence)}</span>
          ${money(i.efficiency)}/week${i.reasons.length?` · ${esc(i.reasons.join(" · "))}`:""}</div></div>
        <div class="amt"><b>${esc(money(i.amount))}</b>${i.headline!==i.amount?`<s>of ${esc(money(i.headline))} headline</s>`:""}</div>
      </div>
      <ol class="steps">${steps.map(([t,txt])=>`<li><time>${esc(t)}</time><span>${txt}</span></li>`).join("")}</ol>
      <div class="notes">${i.caveats.map(c=>`<p>${esc(c)}</p>`).join("")}
        <div class="src"><a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">Read the terms at the source</a>
        <span style="color:var(--ink-3)"> · verified ${esc(i.verified||"—")}</span></div></div>
    </article>`;
  }).join("");
  return renderSchedule(p) + renderNext(p) + cards;
}

function renderAll(){
  const rows = [...OFFERS].sort((a,b)=> g(b,"bonus.amount",0)-g(a,"bonus.amount",0)).map(o=>{
    const dd = g(o,"requirements.direct_deposit",{}) || {};
    let req = "—";
    if(dd.required){
      req = dd.min_amount_cumulative ? `${money(dd.min_amount_cumulative)} in deposits`
          : dd.min_amount_each ? `${dd.count||1} x ${money(dd.min_amount_each)}` : "direct deposit";
      if(dd.window_days) req += ` in ${dd.window_days}d`;
    } else if(g(o,"requirements.min_balance.amount")){
      req = `${money(g(o,"requirements.min_balance.amount"))} balance`;
    }
    const inc = g(o,"eligibility.states_included");
    return `<tr>
      <td><b>${esc(g(o,"bank.name"))}</b><span class="sub">${esc(g(o,"account.name"))}</span></td>
      <td class="n">${esc(money(g(o,"bonus.amount",0)))}</td>
      <td>${esc(req)}<span class="sub">${esc(dd.window_starts ? "from "+dd.window_starts.replace(/_/g," ") : dd.required ? "start unstated" : "")}</span></td>
      <td>${inc ? esc(inc.join(" ")) : "nationwide"}</td>
      <td class="n">${esc(g(o,"dates.deadline") || "open-ended")}</td>
      <td><span class="tag ${esc(g(o,"provenance.confidence"))}">${esc(g(o,"provenance.confidence"))}</span>
        <span class="sub">${esc(g(o,"provenance.last_verified"))}</span></td></tr>`;
  }).join("");
  return `<div class="sheet"><h3>Every tracked offer <em>— ${OFFERS.length}, regardless of whether it fits you</em></h3>
    <div class="tw"><table><thead><tr><th>Bank</th><th class="n">Bonus</th><th>What it takes</th>
    <th>Where</th><th class="n">Closes</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function renderWhy(p){
  const heads = {
    unslotted:["No room on your calendar","These fit your situation, but every opening date collided with a limit you set."],
    infeasible:["Your paycheck can't reach them","The requirement is real money on a real clock, and the arithmetic doesn't close."],
    filter:["Ruled out up front","Wrong state, too small, wrong account type, or you're inside a cooldown."],
  };
  const blocks = ["unslotted","infeasible","filter"].map(stage=>{
    const items = p.rejected.filter(r=>r.stage===stage);
    if(!items.length) return "";
    const rows = items.sort((a,b)=> g(b.offer,"bonus.amount",0)-g(a.offer,"bonus.amount",0)).map(r=>
      `<tr><td><b>${esc(g(r.offer,"bank.name"))}</b><span class="sub">${esc(g(r.offer,"account.name"))}</span></td>
       <td class="n">${esc(money(g(r.offer,"bonus.amount",0)))}</td>
       <td class="reason">${esc(r.reason)}</td></tr>`).join("");
    return `<div class="sheet"><h3>${esc(heads[stage][0])} <em>— ${items.length}. ${esc(heads[stage][1])}</em></h3>
      <div class="tw"><table><thead><tr><th>Bank</th><th class="n">Bonus</th><th>Why it isn't in your ladder</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div>`;
  }).join("");
  return blocks || `<div class="sheet"><div class="empty"><b>Everything made the ladder.</b>Nothing was ruled out.</div></div>`;
}

function planAsText(p){
  const lines = [`BONUS LADDER — generated ${iso(p.today)}`,
    `${p.selected.length} accounts, ${money(p.selected.reduce((s,i)=>s+i.amount,0))} total`, ""];
  p.selected.forEach((i,n)=>{
    lines.push(`${n+1}. ${i.bank} — ${i.account}  ${money(i.amount)}`);
    lines.push(`   open ${iso(i.openDate)} (closes ${iso(i.openBy)})${i.promo?`  code ${i.promo}`:""}`);
    i.ddDates.forEach((d,k)=> lines.push(`   deposit #${k+1} of ${money(i.ddEach)} by ${iso(d)}`));
    if(i.capital) lines.push(`   park ${money(i.capital)} until ${iso(i.capitalFree)}`);
    lines.push(`   bonus posts ${iso(i.bonusPost)}; safe to close ${iso(i.safeClose)}`);
    i.caveats.forEach(c=> lines.push(`   ! ${c}`));
    lines.push(`   ${i.url}`, "");
  });
  lines.push("Not financial advice. Verify every requirement with the bank.");
  return lines.join("\n");
}

function render(){
  const p = lastPlan = buildPlan();
  renderFigures(p);
  document.getElementById("view").innerHTML =
    view==="plan" ? renderLadder(p) : view==="all" ? renderAll() : renderWhy(p);
}

/* ---------------------------------------------------------------- wiring */
function bind(){
  const text = ["state","cadence","nextPay","skipBanks"], nums = ["minBonus","concurrent","capital","ddAmount"];
  for(const id of [...text,...nums]){
    const el = document.getElementById(id);
    el.value = cfg[id];
    el.addEventListener("change", ()=>{
      cfg[id] = nums.includes(id) ? Number(el.value)
              : id==="state" ? el.value.toUpperCase().slice(0,2) : el.value;
      if(id==="state") el.value = cfg.state;
      saveCfg(); render();
    });
  }
  for(const id of ["splittable","business","chex","highOnly","totalMode"]){
    const el = document.getElementById(id);
    el.checked = !!cfg[id];
    el.addEventListener("change", ()=>{ cfg[id]=el.checked; saveCfg(); render(); });
  }
  document.getElementById("reset").addEventListener("click", ()=>{
    cfg = {...DEFAULTS, nextPay: iso(addD(new Date(),7))};
    saveCfg(); bindValues(); render();
  });
  document.querySelectorAll(".tab").forEach(t=>{
    t.addEventListener("click", ()=>{
      view = t.dataset.view;
      document.querySelectorAll(".tab").forEach(x=>x.setAttribute("aria-selected", String(x===t)));
      render();
    });
  });
  const copy = document.getElementById("copyPlan");
  copy.addEventListener("click", async ()=>{
    const text = planAsText(lastPlan || buildPlan());
    try{ await navigator.clipboard.writeText(text); copy.textContent = "Copied"; }
    catch(_){ copy.textContent = "Couldn't copy — select the page instead"; }
    setTimeout(()=>{ copy.textContent = "Copy the ladder as text"; }, 2200);
  });
}
function bindValues(){
  for(const id of ["state","cadence","nextPay","skipBanks","minBonus","concurrent","capital","ddAmount"])
    document.getElementById(id).value = cfg[id];
  for(const id of ["splittable","business","chex","highOnly","totalMode"])
    document.getElementById(id).checked = !!cfg[id];
}

cfg = loadCfg();
const verified = OFFERS.map(o=>g(o,"provenance.last_verified")).filter(Boolean).sort();
document.getElementById("stampCount").textContent = `${OFFERS.length} offers tracked`;
document.getElementById("stampDate").textContent = `data refreshed ${verified[verified.length-1] || BUILT}`;
bind();
render();
})();
</script>
