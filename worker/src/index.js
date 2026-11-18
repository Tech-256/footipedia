// Footipedia live scores + goal alerts — Cloudflare Worker
//
// Needs (Cloudflare dashboard → Worker → Settings):
//   Variables and Secrets: API_KEY (Secret) = API-Football key
//   Bindings: KV namespace, variable name LIVE
//   Trigger events: Cron "* * * * *" (every minute) — checks scores and sends goal alerts
// Optional variable: MIN_TTL = fastest seconds between API calls (default 15; the server also
// spreads the plan's daily allowance so it never runs out).
//
// Endpoints: GET /live (scores for the app), GET /vapid (public key for alerts),
//            POST /subscribe {sub, teams:"all"|[names], ft}, POST /unsubscribe {endpoint}, GET /debug

const LEAGUES = {39:"soccer_epl",40:"soccer_efl_champ",140:"soccer_spain_la_liga",135:"soccer_italy_serie_a",
  78:"soccer_germany_bundesliga",61:"soccer_france_ligue_one",88:"soccer_netherlands_eredivisie",
  94:"soccer_portugal_primeira_liga",179:"soccer_spl",2:"soccer_uefa_champs_league",3:"soccer_uefa_europa_league",
  848:"soccer_uefa_europa_conference_league",36:"afcon_q"};
// Domestic cups of the countries we cover, matched by name + country (ids are learned as they're seen).
const CUPS = [["cup_fa","England",/^FA Cup$/i],["cup_efl","England",/^League Cup$/i],["cup_copa_del_rey","Spain",/^Copa del Rey$/i],
  ["cup_coppa_italia","Italy",/^Coppa Italia$/i],["cup_dfb_pokal","Germany",/^DFB Pokal$/i],["cup_coupe_de_france","France",/^Coupe de France$/i],
  ["cup_knvb","Netherlands",/^KNVB Beker$/i],["cup_taca","Portugal",/^Ta[cç]a de Portugal$/i],["cup_scottish","Scotland",/^(FA Cup|Scottish Cup)$/i],
  ["cup_scottish_lc","Scotland",/^(League Cup|Scottish League Cup)$/i]];
const cupOf = l => { const c = CUPS.find(([k,country,re]) => l.country === country && re.test(l.name)); return c ? c[0] : null; };
const keyOf = l => LEAGUES[l.id] || cupOf(l) || (/africa cup of nations/i.test(l.name) && /qualif/i.test(l.name) ? "afcon_q" : null);
const LIVE_ST = new Set(["1H","HT","2H","ET","BT","P","LIVE","INT","SUSP"]);
const SITE = "https://footipedia.app/";
const CORS = {"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,OPTIONS",
  "Access-Control-Allow-Headers":"Content-Type","Cache-Control":"no-store","Content-Type":"application/json; charset=utf-8"};
const json = (o, s=200) => new Response(JSON.stringify(o), {status:s, headers:CORS});
const today = () => new Date().toISOString().slice(0,10);

// ---------- API-Football ----------
let RL = null;   // last rate-limit headers seen (for /debug)
let KICK = 0;    // last time a visitor request woke the job (per server copy)
const LGFAIL = {}; // league id -> {t, error} of the last failed read (memory only, so failures cost no KV writes)
let LASTCALL = 0;  // space API calls out: back-to-back requests get refused as "too many requests"
async function api(env, path){
  const wait = LASTCALL + 1500 - Date.now();
  if (wait > 0) await new Promise(res => setTimeout(res, wait));
  LASTCALL = Date.now();
  const r = await fetch("https://v3.football.api-sports.io/" + path, {headers:{"x-apisports-key": env.API_KEY}});
  const rem = r.headers.get("x-ratelimit-requests-remaining");
  RL = {t:new Date().toISOString(), path:path.split("?")[0], status:r.status, day_limit:r.headers.get("x-ratelimit-requests-limit"), day_left:rem,
        min_limit:r.headers.get("X-RateLimit-Limit"), min_left:r.headers.get("X-RateLimit-Remaining")};
  const j = await r.json();
  if (!r.ok || (j.errors && Object.keys(j.errors).length)) throw new Error(JSON.stringify(j.errors || r.status));
  return {list: j.response || [], remaining: rem == null ? null : +rem};
}
const slim = x => ({lg:keyOf(x.league), id:x.fixture.id, ko:x.fixture.date, home:x.teams.home.name, away:x.teams.away.name,
  hg:x.goals.home, ag:x.goals.away, st:x.fixture.status.short, min:x.fixture.status.elapsed, extra:x.fixture.status.extra||null, at:Date.now()});

function gap(remaining, minTtl){
  // Spread the calls left today until 23:30 UTC, never faster than minTtl seconds.
  const now = new Date(), end = new Date(now); end.setUTCHours(23,30,0,0);
  const secs = Math.max(1800, (end - now)/1000);
  if (remaining == null) return minTtl;
  if (remaining <= 3) return Math.max(minTtl, (Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate()+1,0,1) - now)/1000);
  return Math.max(minTtl, Math.round(secs / (remaining - 2)));
}
const anyActive = ms => { const now = Date.now();
  return ms.some(m => LIVE_ST.has(m.st) || (m.st === "NS" && now > Date.parse(m.ko) - 3*60e3 && now < Date.parse(m.ko) + 4*36e5)); };

// One update: the full day list (hourly, or on a new day) or just the live matches. Returns goal events.
async function update(env, S, minTtl){
  const now = Date.now(), events = [];
  const newDay = S.day !== today();
  if (!newDay && now < (S.next || 0)) return {changed:false, events};
  const full = newDay || !S.matches || now >= (S.nextFull || 0);
  if (!full && !anyActive(S.matches)) return {changed:false, events};
  const old = {}; (newDay ? [] : S.matches || []).forEach(m => old[m.id] = m);
  let fresh;
  try {
    if (full) {
      const r = await api(env, `fixtures?date=${today()}&timezone=UTC`);
      const mine = r.list.filter(x => keyOf(x.league)); fresh = mine.map(slim); S.remaining = r.remaining;
      S.cupIds = [...new Set([...(S.cupIds || []), ...mine.filter(x => cupOf(x.league)).map(x => x.league.id)])];
      S.nextFull = now + 60*60e3; S.day = today();
    } else {
      const r = await api(env, "fixtures?live=" + [...Object.keys(LEAGUES), ...(S.cupIds || [])].join("-"));
      const live = {}; r.list.filter(x => keyOf(x.league)).map(slim).forEach(m => live[m.id] = m); S.remaining = r.remaining;
      fresh = S.matches.map(m => live[m.id] || (LIVE_ST.has(m.st) ? {...m, st:"FT", at:now} : m));
      Object.values(live).forEach(m => { if (!old[m.id]) fresh.push(m); });
      if (S.matches.some(m => LIVE_ST.has(m.st) && !live[m.id])) S.nextFull = Math.min(S.nextFull || 0, now + 5*60e3);
    }
    S.error = null;
  } catch (e) {
    const msg = String(e.message || e), same = msg === S.error;
    S.error = msg; S.next = now + (/rateLimit|Too many/i.test(msg) ? 20e3 : 5*60e3);
    // save a repeated error at most every 10 minutes (each save is a KV write; the free plan allows 1,000 a day)
    const save = !same || now - (S.errSaved || 0) > 10*60e3; if (save) S.errSaved = now;
    return {changed: save, events};
  }
  S.next = now + gap(S.remaining, minTtl) * 1000; S.updated = new Date(now).toISOString();
  let changed = fresh.length !== Object.keys(old).length;
  for (const m of fresh) {
    const o = old[m.id];
    if (!o || o.st !== m.st || o.hg !== m.hg || o.ag !== m.ag) changed = true;
    if (o && o.hg != null && m.hg != null && m.hg + m.ag > o.hg + o.ag) {
      const side = m.hg > o.hg ? "home" : "away";
      events.push({k:"goal", id:m.id, lg:m.lg, home:m.home, away:m.away, hg:m.hg, ag:m.ag, min:m.min, side, t:now});
    }
    if (o && o.st !== "FT" && m.st === "FT" && o.hg != null) events.push({k:"ft", id:m.id, lg:m.lg, home:m.home, away:m.away, hg:m.hg, ag:m.ag, t:now});
  }
  S.matches = fresh;
  return {changed, events, fullOk: full};   // a successful full read is always saved (keeps "updated" fresh)
}

async function loadState(env){ try { return (await env.LIVE.get("today", "json")) || {}; } catch (e) { return {}; } }
async function saveState(env, S){ try { await env.LIVE.put("today", JSON.stringify(S)); } catch (e) {} }

// ---------- Web Push (RFC 8291 aes128gcm + VAPID, RFC 8292) ----------
const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
const ub64 = s => { s = s.replace(/-/g,"+").replace(/_/g,"/"); while (s.length % 4) s += "="; return Uint8Array.from(atob(s), c => c.charCodeAt(0)); };
const cat = (...a) => { const n = a.reduce((s,x)=>s+x.length,0), o = new Uint8Array(n); let i=0; a.forEach(x=>{o.set(x,i);i+=x.length}); return o; };
const enc = s => new TextEncoder().encode(s);

async function vapidKeys(env){
  let k = null; try { k = await env.LIVE.get("vapid", "json"); } catch (e) {}
  if (k) return k;
  const kp = await crypto.subtle.generateKey({name:"ECDSA", namedCurve:"P-256"}, true, ["sign","verify"]);
  const priv = await crypto.subtle.exportKey("jwk", kp.privateKey);
  const pub = b64u(await crypto.subtle.exportKey("raw", kp.publicKey));
  k = {priv, pub}; await env.LIVE.put("vapid", JSON.stringify(k)); return k;
}
async function hkdf(salt, ikm, info, len){
  const key = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({name:"HKDF", hash:"SHA-256", salt, info}, key, len*8));
}
async function encryptPayload(sub, payload, fixed){
  const uaPub = ub64(sub.keys.p256dh), auth = ub64(sub.keys.auth);
  const as = (fixed && fixed.as) || await crypto.subtle.generateKey({name:"ECDH", namedCurve:"P-256"}, true, ["deriveBits"]);
  const asPub = new Uint8Array(await crypto.subtle.exportKey("raw", as.publicKey));
  const uaKey = await crypto.subtle.importKey("raw", uaPub, {name:"ECDH", namedCurve:"P-256"}, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({name:"ECDH", public:uaKey}, as.privateKey, 256));
  const ikm = await hkdf(auth, shared, cat(enc("WebPush: info\0"), uaPub, asPub), 32);
  const salt = (fixed && fixed.salt) || crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, enc("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await hkdf(salt, ikm, enc("Content-Encoding: nonce\0"), 12);
  const k = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const ct = new Uint8Array(await crypto.subtle.encrypt({name:"AES-GCM", iv:nonce}, k, cat(enc(payload), new Uint8Array([2]))));
  return cat(salt, new Uint8Array([0,0,16,0]), new Uint8Array([asPub.length]), asPub, ct);
}
async function vapidAuth(endpoint, V){
  const aud = new URL(endpoint).origin;
  const h = b64u(enc(JSON.stringify({typ:"JWT", alg:"ES256"})));
  const c = b64u(enc(JSON.stringify({aud, exp: Math.floor(Date.now()/1000) + 12*3600, sub: SITE})));
  const key = await crypto.subtle.importKey("jwk", V.priv, {name:"ECDSA", namedCurve:"P-256"}, false, ["sign"]);
  const sig = await crypto.subtle.sign({name:"ECDSA", hash:"SHA-256"}, key, enc(h + "." + c));
  return `vapid t=${h}.${c}.${b64u(sig)}, k=${V.pub}`;
}
async function sendPush(sub, payload, V){
  const body = await encryptPayload(sub, payload);
  const r = await fetch(sub.endpoint, {method:"POST", body, headers:{
    "Content-Encoding":"aes128gcm", "Content-Type":"application/octet-stream", "TTL":"600", "Urgency":"high",
    "Authorization": await vapidAuth(sub.endpoint, V)}});
  return r.status;
}
const norm = s => String(s).normalize("NFD").replace(/[̀-ͯ]/g,"").toLowerCase().replace(/&/g,"and")
  .replace(/\b(fc|afc|cf|sc|ac|as|rc|bc|cd|ud|ssc|tsg|fsv|vfb|vfl|1\.|islands)\b/g,"").replace(/[^a-z0-9]/g,"");
const ALIAS = {cotedivoire:"ivorycoast", congodr:"drcongo", caboverde:"capeverde"};
const nn = s => { const x = norm(s); return ALIAS[x] || x; };
const sameTeam = (a,b) => { const x = nn(a), y = nn(b); return x === y || (x.length > 3 && y.length > 3 && (x.includes(y) || y.includes(x))); };
const wants = (sub, e) => sub.teams === "all" || (Array.isArray(sub.teams) && sub.teams.some(t => sameTeam(t, e.home) || sameTeam(t, e.away)));

async function notify(env, events){
  if (!events.length) return 0;
  let subs = []; try { subs = (await env.LIVE.get("subs", "json")) || []; } catch (e) {}
  if (!subs.length) return 0;
  const V = await vapidKeys(env); let sent = 0; const dead = new Set();
  for (const e of events) {
    const scorer = e.side === "home" ? e.home : e.away;
    const msg = e.k === "goal"
      ? {title:`⚽ GOAL! ${scorer}${e.min ? " " + e.min + "'" : ""}`, body:`${e.home} ${e.hg}–${e.ag} ${e.away}`, tag:"m"+e.id, url:SITE}
      : {title:"Full time", body:`${e.home} ${e.hg}–${e.ag} ${e.away}`, tag:"m"+e.id, url:SITE};
    for (const s of subs) {
      if (sent >= 35) break;                         // stay under the per-run outgoing request limit
      if (e.k === "ft" && !s.ft) continue;
      if (!wants(s, e)) continue;
      try { const st = await sendPush(s.sub, JSON.stringify(msg), V); sent++; if (st === 404 || st === 410) dead.add(s.sub.endpoint); } catch (_) {}
    }
  }
  if (dead.size) await env.LIVE.put("subs", JSON.stringify(subs.filter(s => !dead.has(s.sub.endpoint))));
  return sent;
}

// ---------- cup fixtures for the next 14 days (one date per quiet minute, each date re-read daily) ----------
async function scanCups(env, S){
  let F = {}; try { F = (await env.LIVE.get("cupfx", "json")) || {}; } catch (e) {}
  const now = Date.now(), days = [];
  for (let i = 1; i <= 14; i++) days.push(new Date(now + i*864e5).toISOString().slice(0,10));
  Object.keys(F).forEach(d => { if (!days.includes(d)) delete F[d]; });
  const due = days.find(d => !F[d] || now - F[d].at > 864e5);
  if (!due) return false;
  try {
    const r = await api(env, `fixtures?date=${due}&timezone=UTC`);
    const cups = r.list.filter(x => cupOf(x.league));
    F[due] = {at: now, m: r.list.filter(x => keyOf(x.league)).map(x => ({lg: keyOf(x.league), id: x.fixture.id, ko: x.fixture.date, home: x.teams.home.name, away: x.teams.away.name,
      round: x.league.round || "", st: x.fixture.status.short}))};
    S.cupIds = [...new Set([...(S.cupIds || []), ...cups.map(x => x.league.id)])];
    await env.LIVE.put("cupfx", JSON.stringify(F));
  } catch (e) {}
  return true;
}

// ---------- league fixtures for the next 14 days, asked league by league ----------
// The whole-day lists above can come back incomplete, so each league is also read on its own
// (one league per quiet minute, each league every 6 hours). /fixtures merges both, by fixture id.
async function scanLeagues(env){
  let L = {}; try { L = (await env.LIVE.get("lgfx", "json")) || {}; } catch (e) {}
  const now = Date.now(), ids = Object.keys(LEAGUES);
  const id = ids.find(i => (!L[i] || now - L[i].at > 6*36e5) && !(LGFAIL[i] && now - LGFAIL[i].t < 15*60e3));
  if (!id) return false;
  try {
    const r = await api(env, `fixtures?league=${id}&next=99&timezone=UTC`);
    const end = now + 15*864e5;
    L[id] = {at: now, m: r.list.filter(x => Date.parse(x.fixture.date) <= end).map(x => ({lg: LEAGUES[id], id: x.fixture.id, ko: x.fixture.date,
      home: x.teams.home.name, away: x.teams.away.name, round: x.league.round || "", st: x.fixture.status.short}))};
    await env.LIVE.put("lgfx", JSON.stringify(L)); delete LGFAIL[id];
  } catch (e) {   // keep the old list; try this league again in 15 minutes
    LGFAIL[id] = {t: now, error: String(e.message || e).slice(0, 200)};
  }
  return true;
}

// ---------- season data for the refresh: standings, results, injuries, top scorers ----------
// One job per quiet minute; each part is re-read every 6 hours (seasons weekly).
const DATA_LEAGUES = {39:"soccer_epl",40:"soccer_efl_champ",140:"soccer_spain_la_liga",135:"soccer_italy_serie_a",78:"soccer_germany_bundesliga",
  61:"soccer_france_ligue_one",88:"soccer_netherlands_eredivisie",94:"soccer_portugal_primeira_liga",179:"soccer_spl",36:"afcon_q"};
const SIX_H = 6*36e5, DAY = 864e5;
async function kvGet(env, k){ try { return await env.LIVE.get(k, "json"); } catch (e) { return null; } }
async function dataJob(env){
  const now = Date.now(), D = (await kvGet(env, "ds")) || {};
  D.seasons = D.seasons || {}; D.sat = D.sat || {}; D.meta = D.meta || {}; D.parts = D.parts || {};
  const ids = Object.keys(DATA_LEAGUES);
  const sid = ids.find(i => !D.seasons[i] || now - (D.sat[i] || 0) > 7*DAY);
  if (sid) {
    const r = await api(env, `leagues?id=${sid}&current=true`);
    const ss = r.list[0]?.seasons || [], se = ss.find(x => x.current) || ss[ss.length - 1];
    if (se) D.seasons[sid] = se.year; D.sat[sid] = now;
    await env.LIVE.put("ds", JSON.stringify(D)); return "season " + sid;
  }
  const jobs = [];
  for (const [id, lg] of Object.entries(DATA_LEAGUES)) for (const part of ["standings","results","injuries","scorers","prev"]) {
    if (lg === "afcon_q" && part !== "standings" && part !== "results") continue;
    const k = `${part}:${id}`, age = now - (D.meta[k] || 0);
    const every = part === "prev" ? 30*DAY : part === "scorers" ? DAY : SIX_H;
    if (age > every) jobs.push([age, id, lg, part, k]);
  }
  if (!jobs.length) return null;
  jobs.sort((a,b) => b[0] - a[0]); const [, id, lg, part, k] = jobs[0]; const season = D.seasons[id];
  const rows = [];
  if (part === "standings") {
    const r = await api(env, `standings?league=${id}&season=${season}`);
    (r.list[0]?.league?.standings || []).forEach(g => g.forEach(t => rows.push([lg, t.group || "", t.team.name, t.all.played, t.all.win, t.all.draw, t.all.lose, t.all.goals.for, t.all.goals.against, t.points,
      t.home.played, t.home.win, t.home.draw, t.home.lose, t.home.goals.for, t.home.goals.against, t.away.played, t.away.win, t.away.draw, t.away.lose, t.away.goals.for, t.away.goals.against].join("|"))));
  } else if (part === "results" || part === "prev") {
    const r = await api(env, `fixtures?league=${id}&season=${part === "prev" ? season - 1 : season}&status=FT-AET-PEN`);
    r.list.forEach(x => { const ft = x.score?.fulltime || {}; if (ft.home == null) return;
      rows.push([lg, x.fixture.date.slice(0,10), x.teams.home.name, x.teams.away.name, ft.home, ft.away, (x.league.round || "").replace(/\|/g,"/")].join("|")); });
  } else if (part === "injuries") {
    const r = await api(env, `injuries?league=${id}&season=${season}`);
    const soon = now + 10*DAY, past = now - 3*36e5;
    r.list.forEach(x => { const t = Date.parse(x.fixture?.date || 0); if (t < past || t > soon) return;
      rows.push([lg, x.team.name, x.player.name, x.player.type || "", (x.player.reason || "").replace(/\|/g,"/"), x.fixture.date].join("|")); });
  } else if (part === "scorers") {
    for (const ep of ["players/topscorers", "players/topassists"]) {
      const r = await api(env, `${ep}?league=${id}&season=${season}`);
      r.list.forEach(x => { const st = x.statistics?.[0] || {};
        rows.push([lg, st.team?.name || "", x.player.name, (st.games?.position || "-")[0], st.goals?.total || 0, st.goals?.assists || 0, st.games?.appearences || 0].join("|")); });
    }
  }
  D.parts[k] = rows; D.meta[k] = now;
  await env.LIVE.put("ds", JSON.stringify(D));
  return k + " " + rows.length;
}

// ---------- the minute job: up to 4 checks, ~15 s apart, one save at the end ----------
async function tick(env){
  const minTtl = +(env.MIN_TTL || 15);
  const S = await loadState(env); let changed = false; const all = [];
  for (let i = 0; i < 4; i++) {
    const r = await update(env, S, minTtl); changed = changed || r.changed || !!r.fullOk;
    if (r.events.length) { all.push(...r.events); await notify(env, r.events); }
    if (i === 3 || !anyActive(S.matches || [])) break;
    const wait = (S.next || 0) - Date.now();
    if (wait > 14000) break;
    await new Promise(res => setTimeout(res, Math.max(wait, 1000)));
  }
  if (all.length) S.events = [...all, ...(S.events || [])].slice(0, 30);
  const ids = (S.cupIds || []).length;
  // Side jobs (next 14 days of fixtures; season data). While matches are live they take turns and run every
  // other minute, so live checks keep the per-minute allowance; when nothing is live both run every minute.
  const busy = anyActive(S.matches || []), m = Math.floor(Date.now() / 60e3);
  if (!busy || m % 4 === 1) { if (!(await scanCups(env, S))) await scanLeagues(env); }
  if (!busy || m % 4 === 3) { try { const j = await dataJob(env); if (j) S.dataError = null; } catch (e) { S.dataError = String(e.message || e); } }
  if (RL) S.rl = RL;
  if (changed || all.length || (S.cupIds || []).length !== ids) await saveState(env, S);
}

export default {
  async scheduled(ev, env, ctx){ ctx.waitUntil(tick(env)); },
  async fetch(req, env, ctx){
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, {headers: CORS});
    if (url.pathname === "/vapid") { const V = await vapidKeys(env); return json({key: V.pub}); }
    if (url.pathname === "/subscribe" && req.method === "POST") {
      const b = await req.json().catch(() => null);
      if (!b || !b.sub || !b.sub.endpoint || !b.sub.keys) return json({ok:false}, 400);
      let subs = []; try { subs = (await env.LIVE.get("subs", "json")) || []; } catch (e) {}
      subs = subs.filter(s => s.sub.endpoint !== b.sub.endpoint);
      subs.push({sub: b.sub, teams: b.teams === "all" ? "all" : (Array.isArray(b.teams) ? b.teams.slice(0, 50).map(String) : []), ft: !!b.ft, t: Date.now()});
      await env.LIVE.put("subs", JSON.stringify(subs.slice(-2000)));
      let test = null;
      if (b.test) { const V = await vapidKeys(env); test = await sendPush(b.sub, JSON.stringify({title:"⚽ Goal alerts are on", body:"You'll get a notification when your teams score.", url:SITE}), V).catch(e => String(e)); }
      return json({ok:true, test});
    }
    if (url.pathname === "/unsubscribe" && req.method === "POST") {
      const b = await req.json().catch(() => null);
      let subs = []; try { subs = (await env.LIVE.get("subs", "json")) || []; } catch (e) {}
      await env.LIVE.put("subs", JSON.stringify(subs.filter(s => !b || s.sub.endpoint !== b.endpoint)));
      return json({ok:true});
    }
    if (url.pathname === "/data") {
      const part = url.searchParams.get("part") || "standings", per = 400, page = +(url.searchParams.get("page") || 1);
      const D = (await kvGet(env, "ds")) || {parts:{}, meta:{}};
      let rows = []; Object.keys(DATA_LEAGUES).forEach(id => { const r = (D.parts || {})[`${part}:${id}`]; if (r) rows.push(...r); });
      if (part === "scorers") rows = [...new Set(rows)];
      const pages = Math.max(1, Math.ceil(rows.length / per));
      const ages = Object.entries(D.meta || {}).filter(([k]) => k.startsWith(part + ":")).map(([k,v]) => k.split(":")[1] + "=" + new Date(v).toISOString().slice(0,16));
      const head = `#part=${part} page=${page}/${pages} rows=${rows.length} seasons=${JSON.stringify(D.seasons || {})} updated ${ages.join(" ")}`;
      return new Response([head, ...rows.slice((page-1)*per, page*per)].join("\n"), {headers:{...CORS, "Content-Type":"text/plain; charset=utf-8"}});
    }
    if (url.pathname === "/fixtures") {
      let F = {}; try { F = (await env.LIVE.get("cupfx", "json")) || {}; } catch (e) {}
      let L = {}; try { L = (await env.LIVE.get("lgfx", "json")) || {}; } catch (e) {}
      const S0 = await loadState(env);
      const all = url.searchParams.get("all") === "1";
      const todays = (S0.day === today() ? S0.matches || [] : []).map(m => ({lg:m.lg, id:m.id, ko:m.ko, home:m.home, away:m.away, st:m.st, hg:m.hg, ag:m.ag}));
      const seen = new Set(), from = today();
      const list = [...todays, ...Object.keys(F).sort().flatMap(d => F[d].m), ...Object.values(L).flatMap(x => x.m).filter(m => m.ko.slice(0,10) >= from)]
        .filter(m => (all || m.lg.startsWith("cup_")) && !seen.has(m.id) && seen.add(m.id))
        .sort((a, b) => Date.parse(a.ko) - Date.parse(b.ko));
      if (url.searchParams.get("format") === "lines")
        return new Response(list.map(m => [m.lg, m.ko, m.home, m.away, m.st, m.hg ?? "", m.ag ?? ""].join("|")).join("\n"), {headers:{...CORS, "Content-Type":"text/plain; charset=utf-8"}});
      return json({days: Object.keys(F).sort(), fixtures: list});
    }
    if (url.pathname !== "/live" && url.pathname !== "/debug") return json({ok:true, use:"/live"});
    const S = await loadState(env);
    // Fallback only if the minute job looks stopped (no full read for 75+ minutes), at most once a minute per server copy,
    // so visitors' requests never turn into extra API calls while the minute job is running.
    const stale = !S.matches || S.day !== today() || Date.now() > Math.max(S.next || 0, Date.parse(S.updated || 0) + 75*60e3) + 120000;
    if (stale && Date.now() - KICK > 60e3) { KICK = Date.now(); ctx.waitUntil(tick(env)); }
    if (url.pathname === "/debug") { let n = 0; try { n = ((await env.LIVE.get("subs","json")) || []).length; } catch(e){}
      let L = {}; try { L = (await env.LIVE.get("lgfx", "json")) || {}; } catch (e) {}
      const leagues = Object.fromEntries(Object.keys(LEAGUES).map(id => { const x = L[id] || {}, f = LGFAIL[id];
        return [LEAGUES[id], {read: x.at && !x.error ? new Date(x.at).toISOString().slice(0,16) : null, fixtures: (x.m || []).length, lastError: f ? new Date(f.t).toISOString().slice(11,16) + " " + f.error : null}]; }));
      return json({updated:S.updated, next:S.next, nextFull:S.nextFull, remaining:S.remaining, error:S.error, dataError:S.dataError||null, rl:S.rl||null, subscribers:n, matches:(S.matches||[]).length, leagues, events:S.events||[]}); }
    return json({updated: S.updated || null, matches: S.matches || [], events: (S.events || []).slice(0, 10), error: S.error || undefined});
  }
};
