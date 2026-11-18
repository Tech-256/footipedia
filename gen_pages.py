#!/usr/bin/env python3
"""Build Footipedia's search-friendly pages from data.json, results.json and preds.json.

Writes (and fully replaces on every run):
  predictions/index.html          all upcoming predictions, by competition
  leagues/<league>/index.html     table, upcoming predictions and recent results per league
  match/<match>/index.html        one page per predicted match (upcoming and recently finished)
  sitemap.xml                     every page above plus the app itself

Usage: python3 gen_pages.py   (run from the repo root, after build.py / cups.py / model.py)
Only reads the three data files; never changes them.
"""
import html, json, os, re, shutil, unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SITE = "https://footipedia.app"
UK = ZoneInfo("Europe/London")
NOW = datetime.now(timezone.utc)
OUT_DIRS = ["predictions", "leagues", "match"]
MIN_GRADED_TO_SHOW_RECORD = 100

data = json.load(open("data.json"))
results = json.load(open("results.json"))
preds = json.load(open("preds.json"))["items"]

LEAGUES = {k: v["name"] for k, v in data["leagues"].items()}
NAMES = dict(LEAGUES)
NAMES.update(data.get("cups", {}).get("names", {}))
NAMES.update({
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "afcon_q": data.get("afcon", {}).get("name", "AFCON qualifiers"),
})
ORDER = list(LEAGUES) + ["soccer_uefa_champs_league", "soccer_uefa_europa_league",
                         "soccer_uefa_europa_conference_league"]

def comp_name(lg):
    return NAMES.get(lg, lg.replace("_", " ").title())

def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

LEAGUE_SLUG = {lg: slug(name) for lg, name in LEAGUES.items()}
e = html.escape

def when(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UK)
    return t.strftime("%a %-d %b %Y, %H:%M")

def day(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UK).strftime("%a %-d %b")

def pct(x):
    return f"{round(x * 100)}%"

def match_slug(p):
    return slug(f"{p['home']} vs {p['away']} {p['time'][:10]}")

def outcome(hg, ag):
    return "home" if hg > ag else "away" if ag > hg else "draw"

def pick_text(p):
    return {"home": f"{p['home']} win", "away": f"{p['away']} win", "draw": "Draw"}[p["pick"]]

# Team lookups from league tables and results
TEAM_LEAGUE, POS = {}, {}
for lg, L in data["leagues"].items():
    for i, row in enumerate(L.get("table", []), 1):
        TEAM_LEAGUE.setdefault(row["team"], lg)
        POS[(lg, row["team"])] = (i, row)

def form(team, n=5):
    games = []
    for lg, lst in results.get("leagues", {}).items():
        for g in lst:
            if g["h"] == team or g["a"] == team:
                games.append(g)
    games.sort(key=lambda g: g["d"])
    out = []
    for g in games[-n:]:
        home = g["h"] == team
        gf, ga = (g["hg"], g["ag"]) if home else (g["ag"], g["hg"])
        r = "W" if gf > ga else "L" if gf < ga else "D"
        out.append((r, g, home))
    return out

def injuries(team):
    lg = TEAM_LEAGUE.get(team)
    return (data.get("injuries", {}).get(lg, {}) or {}).get(team, []) if lg else []

PLAYERS = data.get("players", {})

def team_players(team, n=2):
    lg = TEAM_LEAGUE.get(team)
    rows = [r for r in PLAYERS.get(lg, []) if r["t"] == team and (r["g"] or r["a"])]
    return sorted(rows, key=lambda r: (-r["g"], -r["a"]))[:n]

def injured(team, name):
    for x in injuries(team):
        if x["p"] == name:
            return "doubtful" if x.get("q") else "out"
    return None

def chips(team):
    return "".join(f'<span class="f {r}">{r}</span>' for r, g, _ in form(team))

# ---------- page shell ----------
CSS = """:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e8eaf0;--mut:#8a93a6;--acc:#3ddc97;--warn:#ffb547;--bad:#ff6b6b;--blue:#6aa8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--blue)}header,main,footer{max-width:900px;margin:auto;padding:0 16px}header{padding-top:18px}
.brand{display:flex;align-items:center;gap:10px;color:var(--fg);text-decoration:none;font-weight:700;font-size:18px}
.brand i{width:30px;height:30px;border-radius:8px;background:var(--acc);display:inline-block}
nav.crumbs{font-size:13px;color:var(--mut);margin:12px 0 0}nav.crumbs a{color:var(--mut)}
h1{font-size:24px;margin:10px 0 4px}h2{font-size:17px;margin:22px 0 8px}.sub{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.cta{display:inline-block;background:var(--acc);color:#0b1712;font-weight:700;text-decoration:none;padding:10px 16px;border-radius:10px;margin:8px 0}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:6px 4px;border-bottom:1px solid var(--line);text-align:right}
th:nth-child(2),td:nth-child(2){text-align:left}th{color:var(--mut);font-weight:500}
.bars{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center}.bars b{display:block;font-size:22px}
.list a{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-top:1px solid var(--line);color:var(--fg);text-decoration:none}
.list a:first-child{border-top:0}.list span{color:var(--mut);font-size:13px;white-space:nowrap}
.f{display:inline-block;width:22px;text-align:center;border-radius:5px;font-size:12px;font-weight:700;margin-right:2px}
.f.W{background:rgba(61,220,151,.18);color:var(--acc)}.f.D{background:#232a38;color:var(--mut)}.f.L{background:rgba(255,107,107,.15);color:var(--bad)}
.ok{color:var(--acc)}.no{color:var(--bad)}footer{color:var(--mut);font-size:12px;padding:24px 16px 40px}
.pbar{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:10px 0 2px}.pbar i{display:block}
.pbar .h{background:var(--acc)}.pbar .d{background:#3a4252}.pbar .a{background:var(--blue)}
tr.top td:first-child{box-shadow:inset 3px 0 var(--acc)}tr.bot td:first-child{box-shadow:inset 3px 0 var(--bad)}
.pl{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line)}.pl:first-child{border-top:0}
.pl small{color:var(--mut)}.out{color:var(--bad);font-size:12px;margin-left:4px}
@media(max-width:560px){.hide-s{display:none}}"""

def page(path, title, desc, body, crumbs=(), jsonld=None):
    url = f"{SITE}/{path}"
    crumb_html = ""
    if crumbs:
        crumb_html = '<nav class="crumbs">' + " › ".join(
            f'<a href="/{p}">{e(t)}</a>' if p is not None else e(t) for t, p in crumbs) + "</nav>"
    ld = ""
    if jsonld:
        ld = '<script type="application/ld+json">' + json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/") + "</script>"
    doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{url}">
<meta name="theme-color" content="#0f1115"><link rel="icon" href="/icons/icon.svg"><link rel="apple-touch-icon" href="/icons/icon-180.png">
<meta property="og:type" content="website"><meta property="og:site_name" content="Footipedia">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{SITE}/og.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}"><meta name="twitter:image" content="{SITE}/og.png">
<style>{CSS}</style>{ld}
</head><body>
<header><a class="brand" href="/"><i></i>Footipedia</a>{crumb_html}</header>
<main>{body}
<p><a class="cta" href="/">Open Footipedia: live scores, form & team news →</a></p>
</main>
<footer><p>Footipedia is a free stats project for information and entertainment. Predictions are probabilities, not certainties, and are not betting advice. If you choose to bet, you must be 18+. Bet only what you can afford to lose. Help: <a href="https://www.begambleaware.org" rel="noopener">BeGambleAware.org</a> · 0808 8020 133.</p>
<p>Data: API-Football. Not affiliated with any club, league or competition. Updated {e(data.get('updated', ''))} (UK).</p>
<p><a href="/predictions/">All predictions</a> · {' · '.join(f'<a href="/leagues/{LEAGUE_SLUG[lg]}/">{e(n)}</a>' for lg, n in LEAGUES.items())}</p></footer>
</body></html>
"""
    full = os.path.join(path, "index.html") if not path.endswith(".html") else path
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w") as f:
        f.write(doc)
    return url

def match_row(p):
    res = p.get("res")
    right = ""
    if res:
        score = f"{res['hg']}–{res['ag']}"
        good = outcome(res["hg"], res["ag"]) == p["pick"]
        right = f' <b class="{"ok" if good else "no"}">{"✓" if good else "✗"}</b>'
        tail = f"FT {score}{right}"
    else:
        tail = f"{day(p['time'])} · {pick_text(p)} {pct(max(p['pH'], p['pD'], p['pA']))}"
    return f'<a href="/match/{match_slug(p)}/"><b>{e(p["home"])} vs {e(p["away"])}</b><span>{tail}</span></a>'

# ---------- build ----------
for d in OUT_DIRS:
    shutil.rmtree(d, ignore_errors=True)
urls = [f"{SITE}/", f"{SITE}/predictions/"]

items = sorted(preds.values(), key=lambda p: p["time"])
upcoming = [p for p in items if "res" not in p and datetime.fromisoformat(p["time"].replace("Z", "+00:00")) > NOW - timedelta(hours=3)]
finished = [p for p in items if "res" in p]

# match pages
for p in items:
    lg, h, a = p["lg"], p["home"], p["away"]
    cname = comp_name(lg)
    ms = match_slug(p)
    res = p.get("res")
    title = f"{h} vs {a} prediction, stats & team news – {cname} {day(p['time'])}"
    desc = (f"{h} vs {a} ({cname}, {when(p['time'])} UK): our model gives {h} {pct(p['pH'])}, "
            f"draw {pct(p['pD'])}, {a} {pct(p['pA'])}. Likely score {p['top']}. Form, table and injuries.")
    parts = [f"<h1>{e(h)} vs {e(a)} prediction</h1>",
             f'<div class="sub">{e(cname)} · {e(when(p["time"]))} UK time</div>']
    if res:
        good = outcome(res["hg"], res["ag"]) == p["pick"]
        parts.append(f'<div class="card"><b>Full time: {e(h)} {res["hg"]}–{res["ag"]} {e(a)}</b><br>'
                     f'We predicted: {e(pick_text(p))} — <b class="{"ok" if good else "no"}">{"correct" if good else "wrong"}</b></div>')
    parts.append(f'<div class="card"><div class="bars"><div><b>{pct(p["pH"])}</b>{e(h)} win</div>'
                 f'<div><b>{pct(p["pD"])}</b>Draw</div><div><b>{pct(p["pA"])}</b>{e(a)} win</div></div>'
                 f'<div class="pbar"><i class="h" style="width:{p["pH"]*100:.1f}%"></i><i class="d" style="width:{p["pD"]*100:.1f}%"></i>'
                 f'<i class="a" style="width:{p["pA"]*100:.1f}%"></i></div></div>')
    parts.append(f'<div class="card">Our pick: <b>{e(pick_text(p))}</b> · Most likely score: <b>{e(p["top"])}</b><br>'
                 f'Expected goals: {e(h)} {p["lh"]:.1f}, {e(a)} {p["la"]:.1f} · '
                 f'Over 2.5 goals: {pct(p["o25"])} · Both teams score: {pct(p["btts"])}</div>')
    for team in (h, a):
        tl = TEAM_LEAGUE.get(team)
        bits = []
        if tl and (tl, team) in POS:
            i, row = POS[(tl, team)]
            bits.append(f'{i}{"st" if i % 10 == 1 and i != 11 else "nd" if i % 10 == 2 and i != 12 else "rd" if i % 10 == 3 and i != 13 else "th"} in the '
                        f'<a href="/leagues/{LEAGUE_SLUG[tl]}/">{e(LEAGUES[tl])}</a> · {row["Pts"]} pts from {row["P"]} games · '
                        f'goals {row["GF"]}–{row["GA"]}')
        fm = form(team)
        if fm:
            bits.append("Last 5 (oldest first): " + "".join(f'<span class="f {r}" title="{e(g["h"])} {g["hg"]}–{g["ag"]} {e(g["a"])}">{r}</span>' for r, g, _ in fm))
        kp = team_players(team)
        if kp:
            bits.append("Key players: " + ", ".join(
                f'{e(r["p"])} ({r["g"]} goal{"" if r["g"] == 1 else "s"}, {r["a"]} assist{"" if r["a"] == 1 else "s"})' + (f'<span class="out">{injured(team, r["p"]).upper()}</span>' if injured(team, r["p"]) else "")
                for r in kp))
        inj = injuries(team)
        if inj:
            fx = inj[0].get("fx")
            label = ("Out or doubtful" if not fx or fx == p["time"][:10]
                     else f"Latest injury news (for their {e(fx)} match)")
            bits.append(f"{label}: " + ", ".join(f'{e(x["p"])} ({e(x.get("i") or x.get("t") or "")})' for x in inj[:8]))
        elif tl:
            bits.append("No injuries or suspensions reported.")
        if bits:
            parts.append(f'<h2>{e(team)}</h2><div class="card">' + "<br>".join(bits) + "</div>")
    crumbs = [("Footipedia", ""), ("Predictions", "predictions/")]
    if lg in LEAGUE_SLUG:
        crumbs.append((LEAGUES[lg], f"leagues/{LEAGUE_SLUG[lg]}/"))
    crumbs.append((f"{h} vs {a}", None))
    ld = {"@context": "https://schema.org", "@type": "SportsEvent", "name": f"{h} vs {a}",
          "startDate": p["time"], "sport": "Football",
          "homeTeam": {"@type": "SportsTeam", "name": h}, "awayTeam": {"@type": "SportsTeam", "name": a},
          "superEvent": {"@type": "SportsEvent", "name": cname},
          "eventStatus": "https://schema.org/EventScheduled", "url": f"{SITE}/match/{ms}/"}
    urls.append(page(f"match/{ms}/", title, desc, "\n".join(parts), crumbs, ld))

# league pages
for lg, name in LEAGUES.items():
    L = data["leagues"][lg]
    n_t = len(L.get("table", []))
    rows = "".join(
        f'<tr class="{"top" if i <= 4 else "bot" if i > n_t - 3 else ""}"><td>{i}</td><td>{e(r["team"])}</td><td>{r["P"]}</td>'
        f'<td class="hide-s">{r["W"]}</td><td class="hide-s">{r["D"]}</td>'
        f'<td class="hide-s">{r["L"]}</td><td>{f"{r['GF'] - r['GA']:+d}" if r["GF"] != r["GA"] else "0"}</td><td><b>{r["Pts"]}</b></td><td class="hide-s">{chips(r["team"])}</td></tr>'
        for i, r in enumerate(L.get("table", []), 1))
    up = [p for p in upcoming if p["lg"] == lg]
    recent = sorted(results.get("leagues", {}).get(lg, []), key=lambda g: g["d"])[-10:][::-1]
    body = [f"<h1>{e(name)} table, fixtures & predictions</h1>",
            f'<div class="sub">Updated {e(data.get("updated", ""))} UK</div>',
            "<h2>Upcoming predictions</h2>",
            '<div class="card list">' + ("".join(match_row(p) for p in up) or "No predicted fixtures right now.") + "</div>",
            "<h2>Table</h2>",
            f'<div class="card"><table><tr><th>#</th><th>Team</th><th>P</th><th class="hide-s">W</th><th class="hide-s">D</th>'
            f'<th class="hide-s">L</th><th>GD</th><th>Pts</th><th class="hide-s">Form</th></tr>{rows}</table>'
            '<div class="sub">Top 4 and bottom 3 highlighted. Form: last 5 league games, oldest first.</div></div>']
    pls = PLAYERS.get(lg, [])
    if pls:
        sc = [r for r in pls if r["g"]][:10]
        asx = sorted([r for r in pls if r["a"]], key=lambda r: (-r["a"], -r["g"]))[:5]
        def plrow(r, key):
            o = injured(r["t"], r["p"])
            return (f'<div class="pl"><span><b>{e(r["p"])}</b>' + (f'<span class="out">{o}</span>' if o else "") +
                    f' <small>{e(r["t"])} · {r["m"]} apps</small></span><span><b>{r[key]}</b> <small>{"goals" if key == "g" else "assists"}</small></span></div>')
        body += [f"<h2>{e(name)} top scorers</h2>", '<div class="card">' + "".join(plrow(r, "g") for r in sc) + "</div>",
                 "<h2>Most assists among the top scorers</h2>", '<div class="card">' + "".join(plrow(r, "a") for r in asx) + "</div>"]
    if recent:
        body += ["<h2>Latest results</h2>", '<div class="card">' + "<br>".join(
            f'{e(g["d"])} · {e(g["h"])} {g["hg"]}–{g["ag"]} {e(g["a"])}' for g in recent) + "</div>"]
    title = f"{name} predictions, table & fixtures {results.get('season', '')}".strip()
    desc = f"Free {name} predictions for every upcoming match, plus the live table, top scorers, latest results, form and injury news."
    urls.append(page(f"leagues/{LEAGUE_SLUG[lg]}/", title, desc, "\n".join(body),
                     [("Footipedia", ""), ("Predictions", "predictions/"), (name, None)]))

# predictions hub
by = {}
for p in upcoming:
    by.setdefault(p["lg"], []).append(p)
secs = []
for lg in ORDER + sorted(k for k in by if k not in ORDER):
    if lg not in by:
        continue
    head = f'<a href="/leagues/{LEAGUE_SLUG[lg]}/">{e(comp_name(lg))}</a>' if lg in LEAGUE_SLUG else e(comp_name(lg))
    secs.append(f'<h2>{head}</h2><div class="card list">' + "".join(match_row(p) for p in by[lg]) + "</div>")
graded = len(finished)
right = sum(outcome(p["res"]["hg"], p["res"]["ag"]) == p["pick"] for p in finished)
record = (f'<div class="card">Track record: <b>{right} of {graded}</b> finished matches called correctly '
          f'({round(100 * right / graded)}%).</div>') if graded >= MIN_GRADED_TO_SHOW_RECORD else ""
hub = ["<h1>Football predictions for this week</h1>",
       f'<div class="sub">{len(upcoming)} matches · updated {e(data.get("updated", ""))} UK · '
       "win/draw/win chances, likely scores and goals from our statistical model</div>", record] + secs
page("predictions/", "Free football predictions this week – Premier League, La Liga, Serie A & more",
     "Free predictions for every match this week across the Premier League, Championship, La Liga, Serie A, "
     "Bundesliga, Ligue 1, Eredivisie, Primeira Liga, Scottish Premiership, European cups and domestic cups.",
     "\n".join(hub), [("Footipedia", ""), ("Predictions", None)])

# sitemap
today = NOW.strftime("%Y-%m-%d")
with open("sitemap.xml", "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
    for u in dict.fromkeys(urls):
        f.write(f"  <url><loc>{e(u)}</loc><lastmod>{today}</lastmod></url>\n")
    f.write("</urlset>\n")

print(f"pages: {len(items)} matches, {len(LEAGUES)} leagues, 1 hub; sitemap {len(dict.fromkeys(urls))} urls")
