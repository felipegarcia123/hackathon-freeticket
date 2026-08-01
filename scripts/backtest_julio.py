#!/usr/bin/env python3
"""Backtest LOO limpio del modelo calibrado.

Para cada evento julio_i:
  - Excluye julio_i tanto de los hermanos como de la calibración de bias/σ.
  - Predice usando el modelo con esos parámetros calibrados sobre los otros 31.
  - Compara vs real (checked_in_count).

Reporta cobertura del rango p10-p90 (esperado ~80%), MAPE y bias medio.
"""
from __future__ import annotations
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"

PRIOR_PAID = 0.94
PRIOR_COURTESY = 0.42
PRIOR_MIX = 0.68
CAP_MEMBRESIA = 0.60
CAP_CONSUMO = 0.80
Z80 = 1.2816

# matches
matches: dict[str, tuple[str, float]] = {}
mp = ROOT / "matches.csv"
if mp.exists():
    with mp.open() as f:
        for r in csv.DictReader(f):
            matches[r["sale_id"]] = (r["boom_user_id"], float(r["confidence"]))

# boom rates
boom_stats: dict[str, dict[str, list[int]]] = {}
with (RAW / "boom_tickets.csv").open() as f:
    for r in csv.DictReader(f):
        u = r["boom_user_id"]
        t = r["type"] or "membresia"
        used = 1 if r["used"] == "true" else 0
        if u not in boom_stats:
            boom_stats[u] = {"membresia":[0,0],"consumo_minimo":[0,0]}
        if t not in boom_stats[u]:
            boom_stats[u][t] = [0,0]
        boom_stats[u][t][0] += 1; boom_stats[u][t][1] += used

def boom_prob(uid):
    if uid not in boom_stats: return None
    s = boom_stats[uid]
    m_tot, m_used = s["membresia"]; c_tot, c_used = s["consumo_minimo"]
    if m_tot + c_tot == 0: return None
    p_m = min(m_used/m_tot, CAP_MEMBRESIA) if m_tot else None
    p_c = min(c_used/c_tot, CAP_CONSUMO) if c_tot else None
    if p_m is not None and p_c is not None:
        return (p_m*m_tot + p_c*c_tot)/(m_tot+c_tot)
    return p_m if p_m is not None else p_c

events = {r["event_id"]: r for r in csv.DictReader(open(RAW/"ft_events.csv"))}
artists = {r["artist_id"]: r for r in csv.DictReader(open(RAW/"ft_artists.csv"))}
julio = [e for e in events.values() if e["month"] == "julio"]

tickets_by_event = defaultdict(list)
with (RAW/"ft_tickets.csv").open() as f:
    for r in csv.DictReader(f):
        tickets_by_event[r["event_id"]].append(r)

def hermanos_rate(target, pool):
    if target["is_residency"] != "true": return None, 0
    key = (target["artist_id"], target["venue"], target["weekday"])
    herms = [h for h in pool if (h["artist_id"], h["venue"], h["weekday"]) == key
             and h["event_id"] != target["event_id"]]
    if not herms: return None, 0
    ts = sum(int(h["tickets_sold"] or 0) for h in herms)
    ci = sum(int(h["checked_in_count"] or 0) for h in herms)
    if ts == 0: return None, 0
    return ci/ts, ts

def classify(e, herm_rate, herm_sold):
    if herm_rate is not None and herm_sold >= 100: return "residencia_fuerte"
    if herm_rate is not None: return "residencia_debil"
    if artists.get(e["artist_id"], {}).get("attendance_rate_july", ""): return "artista_con_julio"
    return "fecha_suelta"

def event_factor(kind, herm_rate, e):
    if kind == "residencia_fuerte": return herm_rate/PRIOR_MIX
    if kind == "residencia_debil": return (herm_rate/PRIOR_MIX)*0.7 + 0.3
    if kind == "artista_con_julio":
        try: return float(artists[e["artist_id"]]["attendance_rate_july"])/PRIOR_MIX
        except: return 1.0
    return 1.0

def event_probs(e, pool):
    hr, hs = hermanos_rate(e, pool)
    kind = classify(e, hr, hs)
    factor = event_factor(kind, hr, e)
    ps = []
    for t in tickets_by_event.get(e["event_id"], []):
        sid = t["sale_id"]; tt = t["ticket_type"]
        p_base = None
        if sid in matches and matches[sid][1] >= 0.80:
            p_base = boom_prob(matches[sid][0])
        if p_base is None:
            p_base = PRIOR_COURTESY if tt == "Cortesía" else PRIOR_PAID
        ps.append(max(0.05, min(0.98, p_base*factor)))
    return ps, kind

# Backtest LOO: para cada julio_i, calibrar bias/σ sobre los otros 31
per_event = []
for target in julio:
    real = int(target["checked_in_count"] or 0)
    if not tickets_by_event.get(target["event_id"]): continue
    pool = [e for e in julio if e["event_id"] != target["event_id"]]
    # calibrar bias/σ sobre pool (con hermanos LOO dentro)
    resid_by = defaultdict(list); bias_by = defaultdict(list)
    for e in pool:
        if not tickets_by_event.get(e["event_id"]): continue
        r_pool = [x for x in pool if x["event_id"] != e["event_id"]]
        ps, k = event_probs(e, r_pool)
        m = sum(ps)
        real_e = int(e["checked_in_count"] or 0)
        err = real_e - m
        resid_by[k].append(err*err); bias_by[k].append(err)
    sigma_emp = {k: math.sqrt(sum(v)/len(v)) for k, v in resid_by.items()}
    bias_emp = {k: sum(v)/len(v) for k, v in bias_by.items()}
    # predecir target
    ps, kind = event_probs(target, pool)
    mean = sum(ps) + bias_emp.get(kind, 0.0)
    var_b = sum(p*(1-p) for p in ps)
    sigma_use = max(math.sqrt(var_b), sigma_emp.get(kind, math.sqrt(var_b)))
    sold = len(ps)
    expected = round(max(0, min(sold, mean)))
    p10 = max(0, round(mean - Z80*sigma_use))
    p90 = min(sold, round(mean + Z80*sigma_use))
    cov = 1 if p10 <= real <= p90 else 0
    err = expected - real
    ape = abs(err)/real if real > 0 else 0
    per_event.append({"eid": target["event_id"], "kind": kind, "sold": sold,
                      "real": real, "pred": expected, "p10": p10, "p90": p90,
                      "err": err, "ape": ape, "cov": cov})

print(f"\n=== Backtest LOO limpio sobre {len(per_event)} eventos julio ===\n")
per_kind = defaultdict(list)
for r in per_event: per_kind[r["kind"]].append(r)
for kind, rs in per_kind.items():
    mape = sum(r["ape"] for r in rs)/len(rs)*100
    cov = sum(r["cov"] for r in rs)/len(rs)*100
    bias = sum(r["err"] for r in rs)/len(rs)
    print(f"  {kind:<22} n={len(rs):>2}  MAPE={mape:5.1f}%  cobertura(p10-p90)={cov:5.1f}%  bias_mean={bias:+.1f}")
mape_all = sum(r["ape"] for r in per_event)/len(per_event)*100
cov_all = sum(r["cov"] for r in per_event)/len(per_event)*100
bias_all = sum(r["err"] for r in per_event)/len(per_event)
print(f"\n  GLOBAL                  n={len(per_event):>2}  MAPE={mape_all:5.1f}%  cobertura        ={cov_all:5.1f}%  bias={bias_all:+.1f}")
