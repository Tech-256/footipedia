"""Footipedia: rebuild data.json / results.json from API-Football (via the live-score server).

Usage:  python3 build.py data.json results.json DIR
DIR holds the server's text exports:
  standings.txt  (/data?part=standings, all pages)   lg|group|team|P|W|D|L|GF|GA|Pts|hP|hW|hD|hL|hGF|hGA|aP|aW|aD|aL|aGF|aGA
  results.txt    (/data?part=results)                lg|date|home|away|hg|ag|round
  prev.txt       (/data?part=prev)                   same, last season
  injuries.txt   (/data?part=injuries)               lg|team|player|type|reason|fixture_iso
  scorers.txt    (/data?part=scorers)                lg|team|player|pos|goals|assists|apps
  fixtures.txt   (/fixtures?all=1&format=lines)      lg|kickoff|home|away|status|hg|ag
Lines starting with # are headers. Team names are mapped to the names the app already uses (badges,
saved predictions and followed teams stay the same); the mapping is kept in data["apiNames"].
Each part is only replaced when its file has data and passes its checks; otherwise the old data stays.
"""
import json, os, re, sys, unicodedata
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
UK = ZoneInfo("Europe/London")

DOMESTIC = ["soccer_epl", "soccer_efl_champ", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga",
            "soccer_france_ligue_one", "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga", "soccer_spl"]
UEFA = ["soccer_uefa_champs_league", "soccer_uefa_europa_league", "soccer_uefa_europa_conference_league"]
ALIAS = {"tottenham": "Tottenham Hotspur", "wolves": "Wolverhampton Wanderers", "brighton": "Brighton & Hove Albion",
         "newcastle": "Newcastle United", "westham": "West Ham United", "leeds": "Leeds United", "sheffieldutd": "Sheffield United",
         "manchesterunited": "Manchester United", "manchestercity": "Manchester City", "nottinghamforest": "Nottingham Forest",
         "qpr": "Queens Park Rangers", "westbrom": "West Bromwich Albion", "bayernmunchen": "Bayern Munich",
         "borussiamonchengladbach": "Borussia Monchengladbach", "koln": "FC Koln", "inter": "Inter Milan", "milan": "AC Milan",
         "atalanta": "Atalanta BC", "parissaintgermain": "Paris Saint-Germain", "stadebrestois29": "Brest", "athletic": "Athletic Bilbao",
         "athleticclub": "Athletic Bilbao", "sportingcp": "Sporting Lisbon", "heartofmidlothian": "Hearts", "nec": "NEC", "nijmegen": "NEC",
         "guimaraes": "Vitoria de Guimaraes", "vitoria": "Vitoria de Guimaraes", "twente": "FC Twente", "utrecht": "FC Utrecht",
         "excelsior": "SBV Excelsior", "zwolle": "PEC Zwolle", "gaeagles": "Go Ahead Eagles", "estoril": "Estoril Praia",
         "estrela": "Estrela Amadora", "academicodeviseu": "Academico de Viseu", "olympiquelyonnais": "Lyon", "lyon": "Lyon",
         "marseille": "Marseille", "stadebrestois": "Brest", "hamburgersv": "Hamburger SV", "mainz05": "Mainz 05",
         "unionberlin": "Union Berlin", "paderborn": "SC Paderborn", "heidenheim": "Heidenheim", "rbleipzig": "RB Leipzig",
         "capeverdeislands": "Cape Verde", "congodr": "DR Congo", "cotedivoire": "Ivory Coast"}
DROP = r"\b(fc|afc|cf|sc|ac|as|rc|bc|cd|ud|ssc|tsg|fsv|vfb|vfl|1\.|sv|club|calcio|de|fk)\b"

def norm(s):
    s = unicodedata.normalize("NFD", str(s)); s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", re.sub(DROP, " ", s))

def lines(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p): return []
    return [l.rstrip("\n").split("|") for l in open(p, encoding="utf-8") if l.strip() and not l.startswith("#")]

class Names:
    """API name -> app name, per competition. Persisted so names never flip."""
    def __init__(self, data):
        self.store = data.setdefault("apiNames", {})
        self.known = {lg: [r["team"] for r in data["leagues"].get(lg, {}).get("table", [])] for lg in DOMESTIC}
        self.all = [t for v in self.known.values() for t in v]
        self.unmatched = set()
    def _match(self, name, pool):
        n = norm(name)
        if n in ALIAS and ALIAS[n] in pool: return ALIAS[n]
        by = {norm(t): t for t in pool}
        if n in by: return by[n]
        hits = [t for k, t in by.items() if len(n) > 3 and len(k) > 3 and (n in k or k in n)]
        return hits[0] if len(hits) == 1 else None
    def map(self, lg, name):
        m = self.store.setdefault(lg, {})
        if name in m: return m[name]
        got = self._match(name, self.known.get(lg) or self.all) if lg in DOMESTIC else self._match(name, self.all)
        if got is None and lg in DOMESTIC: got = self._match(name, self.all)
        res = got or name
        if got is None: self.unmatched.add((lg, name))
        if lg in DOMESTIC: m[name] = res      # remember domestic mappings (UEFA/AFCON are looked up each time)
        return res

def run(data_p, results_p, d):
    data = json.load(open(data_p)); results = json.load(open(results_p))
    now = datetime.now(timezone.utc); N = Names(data); report = []
    I = lambda x: int(x) if str(x).strip() not in ("", "None") else 0

    # ---- standings (domestic) ----
    by = defaultdict(list)
    for p in lines(d, "standings.txt"):
        if len(p) >= 22: by[p[0]].append(p)
    for lg in DOMESTIC:
        rows = by.get(lg) or []
        if not rows: report.append(f"{lg}: no standings, kept old"); continue
        T = []
        for p in rows:
            v = [I(x) for x in p[3:22]]
            r = dict(team=N.map(lg, p[2]), P=v[0], W=v[1], D=v[2], L=v[3], GF=v[4], GA=v[5], Pts=v[6],
                     hP=v[7], hW=v[8], hD=v[9], hL=v[10], hGF=v[11], hGA=v[12], aP=v[13], aW=v[14], aD=v[15], aL=v[16], aGF=v[17], aGA=v[18])
            T.append(r)
        ok = all(r["P"] == r["hP"] + r["aP"] and r["GF"] == r["hGF"] + r["aGF"] and r["GA"] == r["hGA"] + r["aGA"] for r in T) \
             and sum(r["hGF"] for r in T) == sum(r["aGA"] for r in T) and len({r["team"] for r in T}) == len(T)
        if not ok: report.append(f"{lg}: standings failed checks, kept old"); continue
        data["leagues"].setdefault(lg, {"name": lg})["table"] = T
    # refresh the name pools now that tables may have changed
    N.known = {lg: [r["team"] for r in data["leagues"][lg]["table"]] for lg in DOMESTIC if lg in data["leagues"]}
    N.all = [t for v in N.known.values() for t in v]

    # ---- results (this season + last season) ----
    def res_from(fname):
        out = defaultdict(list)
        for p in lines(d, fname):
            if len(p) < 6 or p[0] not in DOMESTIC: continue
            out[p[0]].append({"d": p[1], "h": N.map(p[0], p[2]), "a": N.map(p[0], p[3]), "hg": I(p[4]), "ag": I(p[5])})
        return out
    R = res_from("results.txt")
    for lg in DOMESTIC:
        if not R.get(lg): continue
        games = defaultdict(int)
        for x in R[lg]: games[x["h"]] += 1; games[x["a"]] += 1
        P = {r["team"]: r["P"] for r in data["leagues"][lg]["table"]}
        if all(games[t] <= P.get(t, 0) + 1 for t in games) and all(t in P for t in games):
            results.setdefault("leagues", {})[lg] = sorted(R[lg], key=lambda x: x["d"])
        else: report.append(f"{lg}: results don't match the table, kept old")
    PR = res_from("prev.txt")
    for lg in DOMESTIC:
        if PR.get(lg): results.setdefault("prev", {})[lg] = {"season": "2025-26", "matches": sorted(PR[lg], key=lambda x: x["d"])}

    # ---- injuries: who misses each team's next match ----
    sc = {}
    for p in lines(d, "scorers.txt"):
        if len(p) >= 7: sc[(p[0], norm(p[1]), norm(p[2]))] = ({"G": "G", "D": "D", "M": "M", "A": "F", "F": "F"}.get(p[3][:1], "-"), I(p[4]), I(p[5]), I(p[6]))
    inj_rows = defaultdict(list)
    for p in lines(d, "injuries.txt"):
        if len(p) >= 6 and p[0] in DOMESTIC: inj_rows[p[0]].append(p)
    for lg in DOMESTIC:
        if lg not in inj_rows: continue
        nxt = {}
        for p in inj_rows[lg]:
            t = p[5][:10]
            if p[1] not in nxt or t < nxt[p[1]]: nxt[p[1]] = t
        teams = defaultdict(list); seen = set()
        for p in inj_rows[lg]:
            if p[5][:10] != nxt[p[1]]: continue
            team = N.map(lg, p[1]); key = (team, p[2])
            if key in seen: continue
            seen.add(key)
            pos, g, a, apps = sc.get((lg, norm(p[1]), norm(p[2])), ("-", 0, 0, 0))
            reason = p[4] or "Unknown"
            row = {"p": p[2], "pos": pos, "t": "suspension" if re.search(r"suspen|card|red|ban", reason, re.I) else "injury",
                   "i": reason, "r": None, "m": apps, "g": g, "as": a, "fx": nxt[p[1]]}
            if p[3].lower().startswith("question"): row["q"] = True
            teams[team].append(row)
        data.setdefault("injuries", {})[lg] = dict(teams)
        data.setdefault("injUpdated", {})[lg] = now.astimezone(UK).strftime("%-d %B %Y, %H:%M")

    # ---- fixtures: domestic + UEFA + AFCON from the server (cups are merged by cups.py) ----
    fx, keep = [], set()
    for p in lines(d, "fixtures.txt"):
        if len(p) < 5 or p[0].startswith("cup_"): continue
        lg = p[0]
        if lg not in DOMESTIC and lg not in UEFA and lg != "afcon_q": continue
        t = datetime.fromisoformat(p[1].replace("Z", "+00:00")).astimezone(timezone.utc)
        if not (now - timedelta(hours=2) <= t <= now + timedelta(days=14)): continue
        if p[4] not in ("NS", "TBD", "1H", "HT", "2H", "ET", "P", "BT"): continue
        if lg in DOMESTIC: h, a = N.map(lg, p[2]), N.map(lg, p[3])
        elif lg == "afcon_q":
            A = data.get("afcon", {}); pool = [t2 for g in A.get("groups", {}).values() for t2 in g]
            h, a = (N._match(p[2], pool) or p[2]), (N._match(p[3], pool) or p[3])
        else:
            E = data["euro"]
            def em(n):
                n2 = E.get("alias", {}).get(n, n)
                got = N._match(n2, N.all)
                if got: return got
                for k in E.get("clubs", {}):
                    if norm(k) == norm(n2): return k
                return n2
            h, a = em(p[2]), em(p[3])
        k = (lg, h, a, t.strftime("%Y-%m-%dT%H:%M"))
        if k in keep: continue
        keep.add(k); fx.append({"lg": lg, "home": h, "away": a, "time": t.strftime("%Y-%m-%dT%H:%M:%SZ")})
    if fx:
        have = {f["lg"] for f in fx}
        old = [f for f in data["fixtures"] if f["lg"].startswith("cup_") or (f["lg"] not in have and datetime.fromisoformat(f["time"].replace("Z", "+00:00")) > now - timedelta(hours=2))]
        data["fixtures"] = sorted(old + fx, key=lambda f: f["time"])
    else: report.append("fixtures: none received, kept old")

    # ---- AFCON: results + table check ----
    A = data.get("afcon")
    if A:
        pool = [t for g in A["groups"].values() for t in g]; gof = {t: g for g, ts in A["groups"].items() for t in ts}
        ar = []
        for p in lines(d, "results.txt"):
            if len(p) >= 6 and p[0] == "afcon_q":
                h, a = N._match(p[2], pool), N._match(p[3], pool)
                if h and a and gof[h] == gof[a]: ar.append({"d": p[1], "g": gof[h], "h": h, "a": a, "hg": I(p[4]), "ag": I(p[5])})
        st = {}
        for p in lines(d, "standings.txt"):
            if len(p) >= 22 and p[0] == "afcon_q":
                t = N._match(p[2], pool)
                if t: st[t] = (I(p[9]), I(p[7]) - I(p[8]), I(p[3]))
        if ar and st:
            calc = defaultdict(lambda: [0, 0, 0])
            for x in ar:
                for t, gf, ga in ((x["h"], x["hg"], x["ag"]), (x["a"], x["ag"], x["hg"])):
                    c = calc[t]; c[0] += 3 if gf > ga else 1 if gf == ga else 0; c[1] += gf - ga; c[2] += 1
            if all(tuple(calc[t]) == st[t] for t in st) and len(st) == len(pool):
                A["results"] = sorted(ar, key=lambda x: x["d"]); A["updated"] = now.astimezone(UK).strftime("%a %-d %b, %H:%M")
            else: report.append("afcon: API results don't match its table, kept old")

    data["updated"] = now.astimezone(UK).strftime("%a %-d %b, %H:%M")
    data["source"] = "API-Football"
    json.dump(data, open(data_p, "w"), separators=(",", ":"), ensure_ascii=False)
    json.dump(results, open(results_p, "w"), separators=(",", ":"), ensure_ascii=False)
    print("build:", "; ".join(report) or "all parts updated")
    print("unmatched names (kept API name):", sorted(N.unmatched))

if __name__ == "__main__":
    run(*sys.argv[1:4])
