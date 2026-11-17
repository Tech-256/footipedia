"""Footipedia prediction model (v3, with AFCON qualifiers). Mirrors the JavaScript in index.html exactly.

Usage:  python3 model.py data.json results.json preds.json
Adds/refreshes a frozen prediction in preds.json for every fixture whose kick-off
is still in the future, and grades any prediction whose result is now in
results.json. Predictions for matches that have kicked off are never changed.
"""
import json, math, sys
from datetime import datetime, timezone

K = 4          # games of "league average" added to every team (shrinkage)
KF = 3         # same, for the recent-form window
FORM_N = 6     # matches in the recent-form window
FW = 0.25      # weight of recent form vs season average
RHO = -0.1     # Dixon-Coles low-score correction
MODEL_VERSION = 3
EURO_H, EURO_A = 1.55, 1.20   # average home/away goals in UEFA club matches
EST_ATT, EST_DEF = 1.15, 0.85  # assumed domestic rating for clubs from leagues we do not track
CUP_H, CUP_A = 1.45, 1.15   # domestic cups: average home/away goals
AF_HOME = 60   # AFCON qualifiers: home advantage in rating points
AF_I = 25      # rating points at stake per match (FIFA's weight for qualifiers)

# AFCON qualifiers (national teams): a FIFA-style rating. Start = 2155 - 180*ln(FIFA rank at the draw),
# then updated after every qualifier like the FIFA ranking (We = 1/(1+10^(-dr/600)), change = 25*(W-We)).
# Expected goals are set so the Poisson grid gives the same expected result We, around the qualifiers'
# average goals per game.
def afcon_ratings(A):
    R = {t: 2155 - 180 * math.log(r) for t, r in A["rank"].items()}
    for x in A["results"]:
        h, a = x["h"], x["a"]
        if h not in R or a not in R: continue
        we = 1 / (1 + 10 ** (-(R[h] - R[a] + AF_HOME) / 600))
        w = 1.0 if x["hg"] > x["ag"] else 0.5 if x["hg"] == x["ag"] else 0.0
        R[h] += AF_I * (w - we); R[a] -= AF_I * (w - we)
    n = len(A["results"])
    T = min(3.2, max(2.0, sum(x["hg"] + x["ag"] for x in A["results"]) / n)) if n else 2.5
    return R, T

def afcon_predict(A, AR, f):
    R, T = AR
    if f["home"] not in R or f["away"] not in R: return None
    we = 1 / (1 + 10 ** (-(R[f["home"]] - R[f["away"]] + AF_HOME) / 600))
    lo, hi = -T + 0.1, T - 0.1
    for _ in range(40):
        s = (lo + hi) / 2; g = grid((T + s) / 2, (T - s) / 2)
        if g["pH"] + 0.5 * g["pD"] < we: lo = s
        else: hi = s
    s = (lo + hi) / 2
    return grid((T + s) / 2, (T - s) / 2)

def build(data, results):
    M = {}
    for lg, L in data["leagues"].items():
        T = L["table"]
        s = lambda f: sum(r[f] for r in T)
        H = s("hGF") / s("hP"); A = s("aGF") / s("aP"); Mu = s("GF") / s("P")
        sh = lambda g, p, avg: ((g + K * avg) / (p + K)) / avg
        teams = {}
        for i, r in enumerate(T):
            att = sh(r["GF"], r["P"], Mu); de = sh(r["GA"], r["P"], Mu)
            teams[r["team"]] = dict(r=r, pos=i + 1, att=att, de=de,
                attH=(sh(r["hGF"], r["hP"], H) + att) / 2, defH=(sh(r["hGA"], r["hP"], A) + de) / 2,
                attA=(sh(r["aGF"], r["aP"], A) + att) / 2, defA=(sh(r["aGA"], r["aP"], H) + de) / 2)
        R = (results.get("leagues") or {}).get(lg) or []
        for t, tm in teams.items():
            games = sorted([x for x in R if x["h"] == t or x["a"] == t], key=lambda x: x["d"])
            last = games[-FORM_N:]
            if last:
                gf = sum(x["hg"] if x["h"] == t else x["ag"] for x in last)
                ga = sum(x["ag"] if x["h"] == t else x["hg"] for x in last)
                n = len(last)
                ra = ((gf + KF * Mu) / (n + KF)) / Mu; rd = ((ga + KF * Mu) / (n + KF)) / Mu
                tm["fA"] = min(1.15, max(0.85, (1 - FW) + FW * ra / tm["att"]))
                tm["fD"] = min(1.15, max(0.85, (1 - FW) + FW * rd / tm["de"]))
            else:
                tm["fA"] = tm["fD"] = 1.0
        M[lg] = dict(H=H, A=A, Mu=Mu, teams=teams)
    return M

def status(x, md):
    if x.get("fx"):                      # API-Football: listed as missing / doubtful for the next match
        if x.get("q"): return "dbt"
        return "sus" if x["t"] == "suspension" else "out"
    r = x.get("r")
    if x["t"] == "suspension":
        if r and r < md: return "back"
        if r == md: return "dbt"
        return "sus"
    if not r: return "out"
    if r < md: return "back"
    if r == md: return "dbt"
    return "out"

def injury_cut(data, lg, team, md, gf):
    lst = ((data.get("injuries") or {}).get(lg) or {}).get(team) or []
    miss = 0.0
    for x in lst:
        s = status(x, md)
        w = 1.0 if s in ("out", "sus") else 0.5 if s == "dbt" else 0.0
        miss += w * ((x.get("g") or 0) + 0.5 * (x.get("as") or 0))
    share = miss / max(gf, 1)
    return min(0.3, 0.6 * share), miss

def pois(k, l): return math.exp(-l) * l ** k / math.factorial(k)

def grid(lh, la):
    def tau(i, j):
        if i == 0 and j == 0: return 1 - lh * la * RHO
        if i == 0 and j == 1: return 1 + lh * RHO
        if i == 1 and j == 0: return 1 + la * RHO
        if i == 1 and j == 1: return 1 - RHO
        return 1
    g = []; tot = 0
    for i in range(9):
        for j in range(9):
            p = pois(i, lh) * pois(j, la) * tau(i, j); g.append([i, j, p]); tot += p
    pH = pD = pA = o25 = btts = 0
    for s in g:
        s[2] /= tot; i, j, p = s
        if i > j: pH += p
        elif i == j: pD += p
        else: pA += p
        if i + j > 2: o25 += p
        if i and j: btts += p
    g.sort(key=lambda s: -s[2])
    pick = sorted([("home", pH), ("draw", pD), ("away", pA)], key=lambda t: -t[1])[0][0]
    return dict(pH=pH, pD=pD, pA=pA, lh=lh, la=la, o25=o25, btts=btts, pick=pick, top=f"{g[0][0]}-{g[0][1]}")

def cl(x): return min(4.0, max(0.2, x))

def euro_side(data, M, name, md):
    E = data["euro"]
    for lg, m in M.items():
        t = m["teams"].get(name)
        if t:
            cut, _ = injury_cut(data, lg, name, md, t["r"]["GF"])
            s = E["strength"][E["leagueCountry"][lg]]
            return t["att"] * t["fA"] * s, t["de"] * t["fD"] / s, cut
    s = E["strength"].get(E["clubs"].get(name, ""), 0.7)
    return EST_ATT * s, EST_DEF / s, 0.0

def cup_side(data, M, lg_cup, name, md):
    E = data["euro"]
    for lg, m in M.items():
        t = m["teams"].get(name)
        if t:
            cut, _ = injury_cut(data, lg, name, md, t["r"]["GF"])
            s = E["strength"][E["leagueCountry"][lg]]
            return t["att"] * t["fA"] * s, t["de"] * t["fD"] / s, cut
    # A club we don't track (lower division): rated a bit below the weakest club we do track in that country.
    E2 = data["euro"]; att, de = [], []
    for lg in data["cups"].get("leagues", {}).get(lg_cup, []):
        if lg not in M: continue
        s = E2["strength"][E2["leagueCountry"][lg]]
        for t in M[lg]["teams"].values(): att.append(t["att"] * t["fA"] * s); de.append(t["de"] * t["fD"] / s)
    if att: return 0.85 * min(att), 1.15 * max(de), 0.0
    s = data["cups"]["lower"].get(lg_cup, 0.65)
    return s, 1 / s, 0.0

def predict(data, M, f, AR=None):
    md = f["time"][:10]
    if f["lg"].startswith("cup_"):
        ah, dh, ch = cup_side(data, M, f["lg"], f["home"], md); aa, da, ca = cup_side(data, M, f["lg"], f["away"], md)
        return grid(cl(CUP_H * ah * da * (1 - ch)), cl(CUP_A * aa * dh * (1 - ca)))
    if f["lg"] == "afcon_q":
        return afcon_predict(data.get("afcon"), AR, f) if AR else None
    if f["lg"].startswith("soccer_uefa"):
        ah, dh, ch = euro_side(data, M, f["home"], md); aa, da, ca = euro_side(data, M, f["away"], md)
        return grid(cl(EURO_H * ah * da * (1 - ch)), cl(EURO_A * aa * dh * (1 - ca)))
    m = M[f["lg"]]; h = m["teams"].get(f["home"]); a = m["teams"].get(f["away"])
    if not h or not a: return None
    cut_h, _ = injury_cut(data, f["lg"], f["home"], md, h["r"]["GF"])
    cut_a, _ = injury_cut(data, f["lg"], f["away"], md, a["r"]["GF"])
    lh = cl(m["H"] * h["attH"] * a["defA"] * h["fA"] * a["fD"] * (1 - cut_h))
    la = cl(m["A"] * a["attA"] * h["defH"] * a["fA"] * h["fD"] * (1 - cut_a))
    return grid(lh, la)

def key(f): return f'{f["lg"]}|{f["home"]}|{f["away"]}|{f["time"][:10]}'

def run(data_p, results_p, preds_p):
    data = json.load(open(data_p)); results = json.load(open(results_p))
    try: preds = json.load(open(preds_p))
    except Exception: preds = {"v": MODEL_VERSION, "items": {}}
    M = build(data, results); now = datetime.now(timezone.utc)
    AR = afcon_ratings(data["afcon"]) if data.get("afcon") else None
    added = updated = graded = 0
    for f in data["fixtures"]:
        ko = datetime.fromisoformat(f["time"].replace("Z", "+00:00"))
        if ko <= now: continue
        p = predict(data, M, f, AR)
        if not p: continue
        k = key(f); rec = dict(lg=f["lg"], home=f["home"], away=f["away"], time=f["time"],
                               made=now.strftime("%Y-%m-%dT%H:%MZ"), v=MODEL_VERSION,
                               **{k2: (round(v, 4) if isinstance(v, float) else v) for k2, v in p.items()})
        if k in preds["items"]: updated += 1
        else: added += 1
        preds["items"][k] = rec
    allres = []
    for lg, R in (results.get("leagues") or {}).items():
        for x in R: allres.append((lg, x))
    for x in ((data.get("afcon") or {}).get("results") or []):
        if x.get("d"): allres.append(("afcon_q", x))
    for x in ((data.get("cups") or {}).get("results") or []):
        allres.append((x["lg"], x))
    for k, rec in preds["items"].items():
        if "res" in rec: continue
        for lg, x in allres:
            if lg == rec["lg"] and x["h"] == rec["home"] and x["a"] == rec["away"] and abs(
                    (datetime.fromisoformat(x["d"]) - datetime.fromisoformat(rec["time"][:10])).days) <= 3:
                rec["res"] = {"hg": x["hg"], "ag": x["ag"]}; graded += 1; break
    json.dump(preds, open(preds_p, "w"), separators=(",", ":"), ensure_ascii=False)
    print(f"preds: {added} added, {updated} refreshed, {graded} graded, {len(preds['items'])} total")

if __name__ == "__main__":
    run(*sys.argv[1:4])
