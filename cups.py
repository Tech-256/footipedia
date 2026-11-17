"""Footipedia: merge domestic-cup fixtures (from the live-score server) into data.json.

Usage:  python3 cups.py data.json cups.txt
cups.txt = the server's /fixtures?format=lines output: lg|kickoff|home|away|status|hg|ag
Team names are mapped to our league-table names (same country only); clubs we don't track keep
API-Football's name and get an estimated lower-division rating in the model.
Finished ties (FT, 90 minutes) are kept in data["cups"]["results"] so predictions can be graded.
"""
import json, re, sys, unicodedata
from datetime import datetime, timezone, timedelta

CUPS = {
    "cup_fa": ("FA Cup", "ENG", 0.68, ["soccer_epl", "soccer_efl_champ"]),
    "cup_efl": ("EFL Cup", "ENG", 0.70, ["soccer_epl", "soccer_efl_champ"]),
    "cup_copa_del_rey": ("Copa del Rey", "ESP", 0.70, ["soccer_spain_la_liga"]),
    "cup_coppa_italia": ("Coppa Italia", "ITA", 0.80, ["soccer_italy_serie_a"]),
    "cup_dfb_pokal": ("DFB-Pokal", "GER", 0.75, ["soccer_germany_bundesliga"]),
    "cup_coupe_de_france": ("Coupe de France", "FRA", 0.65, ["soccer_france_ligue_one"]),
    "cup_knvb": ("KNVB Cup", "NED", 0.68, ["soccer_netherlands_eredivisie"]),
    "cup_taca": ("Taça de Portugal", "POR", 0.68, ["soccer_portugal_primeira_liga"]),
    "cup_scottish": ("Scottish Cup", "SCO", 0.60, ["soccer_spl"]),
    "cup_scottish_lc": ("Scottish League Cup", "SCO", 0.60, ["soccer_spl"]),
}
ALIAS = {"tottenham": "Tottenham Hotspur", "wolves": "Wolverhampton Wanderers", "brighton": "Brighton & Hove Albion",
         "newcastle": "Newcastle United", "westham": "West Ham United", "leeds": "Leeds United", "sheffieldutd": "Sheffield United",
         "manchesterunited": "Manchester United", "manutd": "Manchester United", "manchestercity": "Manchester City",
         "nottinghamforest": "Nottingham Forest", "qpr": "Queens Park Rangers", "westbrom": "West Bromwich Albion",
         "bayernmunchen": "Bayern Munich", "borussiamonchengladbach": "Borussia Monchengladbach", "koln": "FC Koln",
         "inter": "Inter Milan", "milan": "AC Milan", "atalanta": "Atalanta BC", "parissaintgermain": "Paris Saint-Germain",
         "stadebrestois29": "Brest", "athletic": "Athletic Bilbao", "athleticclub": "Athletic Bilbao", "sportingcp": "Sporting Lisbon",
         "braga": "Braga", "porto": "Porto", "heartofmidlothian": "Hearts", "eintrachtfrankfurt": "Eintracht Frankfurt",
         "bayerleverkusen": "Bayer Leverkusen", "borussiadortmund": "Borussia Dortmund", "rbleipzig": "RB Leipzig",
         "olympiquelyonnais": "Lyon", "olympiquemarseille": "Marseille", "psveindhoven": "PSV Eindhoven", "twente": "FC Twente",
         "utrecht": "FC Utrecht", "ajax": "Ajax", "nec": "NEC", "nijmegen": "NEC", "guimaraes": "Vitoria de Guimaraes"}
DROP = r"\b(fc|afc|cf|sc|ac|as|rc|bc|cd|ud|ssc|tsg|fsv|vfb|vfl|1\.|sv|club|de|calcio)\b"

def norm(s):
    s = unicodedata.normalize("NFD", s); s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", re.sub(DROP, " ", s))

def mapper(data, leagues):
    teams = [r["team"] for lg in leagues for r in data["leagues"].get(lg, {}).get("table", [])]
    by = {norm(t): t for t in teams}
    def m(name):
        n = norm(name)
        if n in ALIAS and ALIAS[n] in teams: return ALIAS[n]
        if n in by: return by[n]
        hits = [t for k, t in by.items() if len(n) > 3 and len(k) > 3 and (n in k or k in n)]
        return hits[0] if len(hits) == 1 else name
    return m

def run(data_p, lines_p):
    data = json.load(open(data_p))
    now = datetime.now(timezone.utc)
    C = data.setdefault("cups", {})
    C["names"] = {k: v[0] for k, v in CUPS.items()}
    C["country"] = {k: v[1] for k, v in CUPS.items()}
    C["lower"] = {k: v[2] for k, v in CUPS.items()}
    C["leagues"] = {k: v[3] for k, v in CUPS.items()}
    res = {(r["lg"], r["h"], r["a"], r["d"]): r for r in C.get("results", [])}
    maps = {k: mapper(data, v[3]) for k, v in CUPS.items()}
    fx, seen = [], set()
    for line in open(lines_p, encoding="utf-8"):
        p = line.strip().split("|")
        if len(p) < 5 or p[0] not in CUPS: continue
        lg, ko, h, a, st = p[:5]; hg, ag = (p[5] if len(p) > 5 else ""), (p[6] if len(p) > 6 else "")
        h, a = maps[lg](h), maps[lg](a)
        t = datetime.fromisoformat(ko.replace("Z", "+00:00")).astimezone(timezone.utc)
        iso = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        if st == "FT" and hg != "" and ag != "":
            res[(lg, h, a, iso[:10])] = {"lg": lg, "h": h, "a": a, "d": iso[:10], "hg": int(hg), "ag": int(ag)}
        if (lg, h, a, iso) in seen: continue
        if now - timedelta(hours=2) <= t <= now + timedelta(days=14) and st in ("NS", "TBD", "1H", "HT", "2H", "ET", "P", "BT"):
            fx.append({"lg": lg, "home": h, "away": a, "time": iso}); seen.add((lg, h, a, iso))
    old = [f for f in data["fixtures"] if not f["lg"].startswith("cup_")]
    data["fixtures"] = old + sorted(fx, key=lambda f: f["time"])
    cutoff = (now - timedelta(days=120)).strftime("%Y-%m-%d")
    C["results"] = sorted([r for r in res.values() if r["d"] >= cutoff], key=lambda r: r["d"])
    json.dump(data, open(data_p, "w"), separators=(",", ":"), ensure_ascii=False)
    tracked = sum(1 for f in fx for t in (f["home"], f["away"]) if any(t == r["team"] for L in data["leagues"].values() for r in L["table"]))
    print(f"cups: {len(fx)} fixtures ({tracked} of {2*len(fx)} teams matched to our tables), {len(C['results'])} results kept")

if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
