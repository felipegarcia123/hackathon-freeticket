#!/usr/bin/env python3
"""Proyección de asistencia para eventos de agosto.

Para cada ticket i asigna p_i por la señal más específica disponible:
  1. Match Boom con score ≥ 0.80 → use_rate desagregado por tipo Boom
     (rate_membresia cap 0.60, rate_consumo cap 0.80).
  2. Sin match Boom → prior por tipo FT: pagado 0.94, cortesía 0.42.
  3. Ajuste por tipo de evento (factor multiplicativo, clip [0.05, 0.98]):
     - Residencia con hermanos julio: factor = tasa_hermanos / 0.68 (prior mezcla).
     - Fecha suelta con attendance_rate_july del artista: factor = arj / 0.68.
     - Nada: sin factor.

p10/p90: σ_empírico calibrado sobre backtest julio (leave-one-out).
Usamos max(σ_bernoulli, σ_residual_por_tipo) para no subestimar el rango.

Salida: forecast.csv con event_id, expected_attendance, p10, p90.
"""
from __future__ import annotations
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "forecast.csv"

PRIOR_PAID = 0.94
PRIOR_COURTESY = 0.42
PRIOR_MIX = 0.68              # mezcla observada en julio (ancla para factores)
CAP_MEMBRESIA = 0.60
CAP_CONSUMO = 0.80
Z80 = 1.2816                  # normal para 80% central (p10 y p90)

# ------------------------------------------------------------------ matches
matches: dict[str, tuple[str, float]] = {}
mp = ROOT / "matches.csv"
if mp.exists():
    with mp.open() as f:
        for r in csv.DictReader(f):
            matches[r["sale_id"]] = (r["boom_user_id"], float(r["confidence"]))
print(f"[fc] matches cargados: {len(matches)}", file=sys.stderr)

# ------------------------------------------------------------------ boom rates por (user, type)
boom_stats: dict[str, dict[str, list[int]]] = {}
with (RAW / "boom_tickets.csv").open() as f:
    for r in csv.DictReader(f):
        u = r["boom_user_id"]
        t = r["type"] or "membresia"
        used = 1 if r["used"] == "true" else 0
        if u not in boom_stats:
            boom_stats[u] = {"membresia": [0, 0], "consumo_minimo": [0, 0]}
        if t not in boom_stats[u]:
            boom_stats[u][t] = [0, 0]
        boom_stats[u][t][0] += 1
        boom_stats[u][t][1] += used

def boom_prob(uid: str) -> float | None:
    if uid not in boom_stats:
        return None
    s = boom_stats[uid]
    m_tot, m_used = s["membresia"]
    c_tot, c_used = s["consumo_minimo"]
    if m_tot + c_tot == 0:
        return None
    p_m = min(m_used / m_tot, CAP_MEMBRESIA) if m_tot else None
    p_c = min(c_used / c_tot, CAP_CONSUMO) if c_tot else None
    if p_m is not None and p_c is not None:
        return (p_m * m_tot + p_c * c_tot) / (m_tot + c_tot)
    return p_m if p_m is not None else p_c

# ------------------------------------------------------------------ eventos, artistas
events: dict[str, dict] = {}
with (RAW / "ft_events.csv").open() as f:
    for r in csv.DictReader(f):
        events[r["event_id"]] = r

artists: dict[str, dict] = {}
with (RAW / "ft_artists.csv").open() as f:
    for r in csv.DictReader(f):
        artists[r["artist_id"]] = r

julio_events = [e for e in events.values() if e["month"] == "julio"]

# ------------------------------------------------------------------ tickets por evento y sales
tickets_by_event: dict[str, list[dict]] = defaultdict(list)
with (RAW / "ft_tickets.csv").open() as f:
    for r in csv.DictReader(f):
        tickets_by_event[r["event_id"]].append(r)

# ------------------------------------------------------------------ func: clasificar evento y estimar prob por ticket

def hermanos_rate(target: dict, exclude_self: bool = False) -> tuple[float | None, int]:
    """Tasa promedio de hermanos julio con mismo (artist, venue, weekday)."""
    if target["is_residency"] != "true":
        return None, 0
    key = (target["artist_id"], target["venue"], target["weekday"])
    herms = [h for h in julio_events
             if (h["artist_id"], h["venue"], h["weekday"]) == key
             and (not exclude_self or h["event_id"] != target["event_id"])]
    if not herms:
        return None, 0
    ts_sum = sum(int(h["tickets_sold"] or 0) for h in herms)
    ci_sum = sum(int(h["checked_in_count"] or 0) for h in herms)
    if ts_sum == 0:
        return None, 0
    return ci_sum / ts_sum, ts_sum

def classify_event(e: dict, herm_rate: float | None, herm_sold: int) -> str:
    if herm_rate is not None and herm_sold >= 100:
        return "residencia_fuerte"
    if herm_rate is not None:
        return "residencia_debil"
    art = artists.get(e["artist_id"], {})
    if art.get("attendance_rate_july", ""):
        return "artista_con_julio"
    return "fecha_suelta"

def event_factor(kind: str, herm_rate: float | None, e: dict) -> float:
    """Factor multiplicativo sobre p_base, ancla en PRIOR_MIX (0.68)."""
    if kind == "residencia_fuerte":
        return herm_rate / PRIOR_MIX
    if kind == "residencia_debil":
        return (herm_rate / PRIOR_MIX) * 0.7 + 0.3
    if kind == "artista_con_julio":
        try:
            arj = float(artists[e["artist_id"]]["attendance_rate_july"])
            return arj / PRIOR_MIX
        except Exception:
            return 1.0
    return 1.0

def prob_for_ticket(t: dict) -> float:
    sid = t["sale_id"]
    tt = t["ticket_type"]
    p_base = None
    if sid in matches and matches[sid][1] >= 0.80:
        p_base = boom_prob(matches[sid][0])
    if p_base is None:
        p_base = PRIOR_COURTESY if tt == "Cortesía" else PRIOR_PAID
    return p_base

def event_probs(e: dict, exclude_self_for_herm: bool = False) -> tuple[list[float], str, float | None]:
    herm_rate, herm_sold = hermanos_rate(e, exclude_self=exclude_self_for_herm)
    kind = classify_event(e, herm_rate, herm_sold)
    factor = event_factor(kind, herm_rate, e)
    probs = []
    for t in tickets_by_event.get(e["event_id"], []):
        p_base = prob_for_ticket(t)
        p = max(0.05, min(0.98, p_base * factor))
        probs.append(p)
    return probs, kind, herm_rate

# ------------------------------------------------------------------ CALIBRACIÓN: σ empírico por tipo, leave-one-out julio
residuals_by_kind: dict[str, list[float]] = defaultdict(list)
bias_by_kind: dict[str, list[float]] = defaultdict(list)
n_by_kind: dict[str, int] = defaultdict(int)

for e in julio_events:
    real = int(e["checked_in_count"] or 0)
    tks = tickets_by_event.get(e["event_id"], [])
    if not tks:
        continue
    probs, kind, _ = event_probs(e, exclude_self_for_herm=True)
    mean = sum(probs)
    err = real - mean
    residuals_by_kind[kind].append(err * err)
    bias_by_kind[kind].append(err)
    n_by_kind[kind] += 1

sigma_emp: dict[str, float] = {}
bias_emp: dict[str, float] = {}
for kind in residuals_by_kind:
    n = n_by_kind[kind]
    sigma_emp[kind] = math.sqrt(sum(residuals_by_kind[kind]) / n) if n else 0.0
    bias_emp[kind] = sum(bias_by_kind[kind]) / n if n else 0.0
print(f"[fc] σ empírico por tipo: { {k: f'{v:.1f}' for k, v in sigma_emp.items()} }", file=sys.stderr)
print(f"[fc] bias empírico (real-pred): { {k: f'{v:+.1f}' for k, v in bias_emp.items()} }", file=sys.stderr)

# ------------------------------------------------------------------ FORECAST agosto
rows_out = []
diag_counter = defaultdict(int)

for eid, e in events.items():
    if e["month"] != "agosto":
        continue
    tks = tickets_by_event.get(eid, [])
    if not tks:
        rows_out.append([eid, 0, 0, 0])
        continue
    probs, kind, _ = event_probs(e, exclude_self_for_herm=False)
    diag_counter[kind] += 1

    mean = sum(probs)
    # ajuste por bias observado sobre julio (mismo tipo)
    mean_adj = mean + bias_emp.get(kind, 0.0)

    var_bern = sum(p * (1 - p) for p in probs)
    sigma_bern = math.sqrt(var_bern)
    sigma_use = max(sigma_bern, sigma_emp.get(kind, sigma_bern))

    tickets_sold = len(probs)
    expected = round(max(0, min(tickets_sold, mean_adj)))
    p10 = max(0, round(mean_adj - Z80 * sigma_use))
    p90 = min(tickets_sold, round(mean_adj + Z80 * sigma_use))
    rows_out.append([eid, expected, p10, p90])

with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["event_id", "expected_attendance", "p10", "p90"])
    for row in rows_out:
        w.writerow(row)

# ------------------------------------------------------------------ JSON enriquecido para la web (raw/ no se sube al repo)
import json
STAFF_PER = 80  # 1 persona en puerta por cada 80 asistentes esperados
web_rows = []
row_by_eid = {r[0]: r for r in rows_out}
for eid, e in events.items():
    if e["month"] != "agosto" or eid not in row_by_eid:
        continue
    _, expected, p10, p90 = row_by_eid[eid]
    tickets_sold = int(e.get("tickets_sold") or 0)
    capacity = int(e.get("capacity") or 0)
    staff = max(2, math.ceil(expected / STAFF_PER))
    web_rows.append({
        "event_id": eid,
        "title": e.get("title", ""),
        "artist": e.get("artist_name", ""),
        "venue": e.get("venue", ""),
        "city": e.get("city", ""),
        "starts_at": e.get("starts_at", ""),
        "weekday": e.get("weekday", ""),
        "capacity": capacity,
        "tickets_sold": tickets_sold,
        "expected_attendance": expected,
        "p10": p10,
        "p90": p90,
        "staff_puerta": staff,
        "is_residency": e.get("is_residency") == "true",
    })
web_rows.sort(key=lambda r: r["starts_at"])
(ROOT / "data.json").write_text(json.dumps(web_rows, ensure_ascii=False, indent=2))

print(f"[fc] {len(rows_out)} eventos de agosto proyectados", file=sys.stderr)
print(f"[fc] mix por tipo: {dict(diag_counter)}", file=sys.stderr)
print(f"[fc] escrito {OUT}", file=sys.stderr)
print(f"[fc] escrito {ROOT / 'data.json'} ({len(web_rows)} eventos para la web)", file=sys.stderr)
