#!/usr/bin/env python3
"""Proyección de asistencia por evento de agosto.

Para cada ticket i asigna p_i por la señal más específica disponible:
  1. Match Boom con score ≥ 0.80 → use_rate desagregado por tipo Boom
     (rate_membresia cap 0.60, rate_consumo cap 0.80).
  2. Sin Boom pero tipo FT claro → General/Preferencial/VIP 0.94, Cortesía 0.42.
  3. Ajuste por evento (factor multiplicativo, clip [0.05, 0.98]):
     - Residencia con hermanos en julio (mismo artist_id+venue+weekday):
       factor = attendance_rate_hermanos_promedio / prior_del_tipo.
     - Fecha suelta con attendance_rate_july del artista → factor suave [0.85, 1.15].
     - Nada → sin factor.

p10/p90 por normal-approx sobre suma de Bernoullis:
  mean = Σ p_i
  var  = Σ p_i(1-p_i)
  σ_modelo = k * sqrt(var), k = 1.0 residencia con hermano, 1.3 mezcla, 1.8 fecha suelta
  p10 = max(0, mean - 1.2816 * σ)
  p90 = min(tickets_sold, mean + 1.2816 * σ)
"""
from __future__ import annotations
import csv
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "forecast.csv"

PRIOR_PAID = 0.94
PRIOR_COURTESY = 0.42
PRIOR_BOOM_MEMBRESIA_CAP = 0.60
PRIOR_BOOM_CONSUMO_CAP = 0.80

# ------------------------------------------------------------------ load matches
matches: dict[str, tuple[str, float]] = {}
matches_path = ROOT / "matches.csv"
if matches_path.exists():
    with matches_path.open() as f:
        for r in csv.DictReader(f):
            matches[r["sale_id"]] = (r["boom_user_id"], float(r["confidence"]))
print(f"[fc] {len(matches)} matches cargados")

# ------------------------------------------------------------------ boom tickets → use_rate por (user, type)
boom_stats: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"membresia": [0, 0], "consumo_minimo": [0, 0]})
with (RAW / "boom_tickets.csv").open() as f:
    for r in csv.DictReader(f):
        u = r["boom_user_id"]
        t = r["type"] or "membresia"
        used = 1 if r["used"] == "true" else 0
        if t not in boom_stats[u]:
            boom_stats[u][t] = [0, 0]
        boom_stats[u][t][0] += 1        # total
        boom_stats[u][t][1] += used     # used

def boom_prob(user_id: str) -> float | None:
    """Prob de asistencia para un boom user, ponderando membresia vs consumo_minimo."""
    if user_id not in boom_stats:
        return None
    s = boom_stats[user_id]
    m_tot, m_used = s["membresia"]
    c_tot, c_used = s["consumo_minimo"]
    total = m_tot + c_tot
    if total == 0:
        return None
    # tasas individuales con techo
    p_m = min(m_used / m_tot, PRIOR_BOOM_MEMBRESIA_CAP) if m_tot else None
    p_c = min(c_used / c_tot, PRIOR_BOOM_CONSUMO_CAP) if c_tot else None
    # ponderar por conteos
    if p_m is not None and p_c is not None:
        return (p_m * m_tot + p_c * c_tot) / total
    return p_m if p_m is not None else p_c

# ------------------------------------------------------------------ eventos
events: dict[str, dict] = {}
with (RAW / "ft_events.csv").open() as f:
    for r in csv.DictReader(f):
        events[r["event_id"]] = r

# artistas → attendance_rate_july
artists: dict[str, dict] = {}
with (RAW / "ft_artists.csv").open() as f:
    for r in csv.DictReader(f):
        artists[r["artist_id"]] = r

# ------------------------------------------------------------------ hermanos: agrupar julio por (artist,venue,weekday)
julio_by_key: dict[tuple, list[dict]] = defaultdict(list)
for eid, e in events.items():
    if e["month"] == "julio":
        key = (e["artist_id"], e["venue"], e["weekday"])
        julio_by_key[key].append(e)

def hermanos_rate(agosto_event: dict) -> tuple[float | None, int]:
    """Devuelve (tasa_promedio_ponderada, tickets_sold_totales_hermanos)."""
    if agosto_event["is_residency"] != "true":
        return None, 0
    key = (agosto_event["artist_id"], agosto_event["venue"], agosto_event["weekday"])
    herms = julio_by_key.get(key, [])
    if not herms:
        return None, 0
    total_sold = 0
    total_att = 0
    for h in herms:
        ts = int(h["tickets_sold"] or 0)
        ci = int(h["checked_in_count"] or 0)
        total_sold += ts
        total_att += ci
    if total_sold == 0:
        return None, 0
    return total_att / total_sold, total_sold

# ------------------------------------------------------------------ tickets → por evento
tickets_by_event: dict[str, list[dict]] = defaultdict(list)
sales_event: dict[str, str] = {}
with (RAW / "ft_sales.csv").open() as f:
    for r in csv.DictReader(f):
        sales_event[r["sale_id"]] = r["event_id"]

with (RAW / "ft_tickets.csv").open() as f:
    for r in csv.DictReader(f):
        tickets_by_event[r["event_id"]].append(r)

# ------------------------------------------------------------------ compute forecast
rows_out = []
diag_counter = defaultdict(int)

for eid, e in events.items():
    if e["month"] != "agosto":
        continue
    tks = tickets_by_event.get(eid, [])
    if not tks:
        rows_out.append([eid, 0, 0, 0])
        continue

    # tipo de evento → factor + k
    herm_rate, herm_sold = hermanos_rate(e)
    if herm_rate is not None and herm_sold >= 100:
        event_kind = "residencia_fuerte"
        k_sigma = 1.0
    elif herm_rate is not None:
        event_kind = "residencia_debil"
        k_sigma = 1.3
    else:
        # sin hermano; ver si artista tiene attendance_rate_july
        art = artists.get(e["artist_id"], {})
        arj = art.get("attendance_rate_july", "")
        if arj:
            event_kind = "artista_con_julio"
            k_sigma = 1.5
        else:
            event_kind = "fecha_suelta"
            k_sigma = 1.8
    diag_counter[event_kind] += 1

    probs = []
    for t in tks:
        sid = t["sale_id"]
        tt = t["ticket_type"]
        # 1. base por Boom si hay match confiable
        p_base = None
        if sid in matches:
            _, conf = matches[sid]
            if conf >= 0.80:
                p_base = boom_prob(matches[sid][0])
        # 2. si no, prior por tipo de ticket
        if p_base is None:
            if tt == "Cortesía":
                p_base = PRIOR_COURTESY
            else:
                p_base = PRIOR_PAID
        # 3. factor por evento
        factor = 1.0
        if event_kind in ("residencia_fuerte", "residencia_debil") and herm_rate is not None:
            # ancla en prior del tipo (mezcla ~ 0.68 para julio, mejor usar herm_rate directo)
            # el factor es sobre p_base: proyectamos que la prob individual se mueve
            # proporcional a como se movió la asistencia agregada del hermano
            # prior mixto de referencia (aprox 0.68 = 94% pagos * mix pagos + 42% cortesías * mix cortesías)
            # más simple: usar herm_rate como ancla y ponderar (0.65 señal julio, 0.35 base)
            factor = (herm_rate / 0.65) if event_kind == "residencia_fuerte" else (herm_rate / 0.65) * 0.7 + 0.3
        elif event_kind == "artista_con_julio":
            try:
                arj = float(artists[e["artist_id"]]["attendance_rate_july"])
                factor = 0.85 + 0.30 * (arj - 0.5)  # suave, bounded
            except Exception:
                factor = 1.0
        p = max(0.05, min(0.98, p_base * factor))
        probs.append(p)

    mean = sum(probs)
    var = sum(p * (1 - p) for p in probs)
    sigma = k_sigma * math.sqrt(var)
    tickets_sold = len(probs)
    expected = round(mean)
    p10 = max(0, round(mean - 1.2816 * sigma))
    p90 = min(tickets_sold, round(mean + 1.2816 * sigma))
    rows_out.append([eid, expected, p10, p90])

# ------------------------------------------------------------------ write
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["event_id", "expected_attendance", "p10", "p90"])
    for row in rows_out:
        w.writerow(row)

print(f"[fc] {len(rows_out)} eventos de agosto proyectados")
print(f"[fc] mix por tipo de evento: {dict(diag_counter)}")
print(f"[fc] escrito {OUT}")
