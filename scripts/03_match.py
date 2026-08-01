#!/usr/bin/env python3
"""Cruce sale_id ↔ boom_user_id con score de confianza.

Estrategia:
  - Normalización de email/phone/name.
  - Blocking por email_norm, phone_last10, last_token → candidatos.
  - Jerarquía de reglas (primer hit gana), umbral publicado ≥ 0.80.
  - Anti-invento: ambigüedad (top1 - top2 ≤ 0.05 y ambos ≥ 0.80) → descartar.

Salida: matches.csv con header sale_id,boom_user_id,confidence
"""
from __future__ import annotations
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "matches.csv"

DOMAIN_FIX = {
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gnail.com": "gmail.com",
    "hotmial.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "outlok.com": "outlook.com",
    "outllok.com": "outlook.com",
    "yhaoo.com": "yahoo.com",
    "yaho.com": "yahoo.com",
}

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def norm_email(e: str) -> str:
    if not e:
        return ""
    e = e.strip().lower()
    if "@" not in e:
        return ""
    local, dom = e.split("@", 1)
    # quitar +alias
    if "+" in local:
        local = local.split("+", 1)[0]
    dom = DOMAIN_FIX.get(dom, dom)
    return f"{local}@{dom}"

def email_local(e: str) -> str:
    return e.split("@", 1)[0] if "@" in e else ""

def email_domain(e: str) -> str:
    return e.split("@", 1)[1] if "@" in e else ""

def norm_phone(p: str) -> str:
    """Últimos 10 dígitos. Boom viene con 10 dígitos limpios."""
    if not p:
        return ""
    digits = re.sub(r"\D", "", p)
    return digits[-10:] if len(digits) >= 10 else ""

def norm_name(n: str) -> tuple[list[str], set[str]]:
    """Devuelve (tokens_ordenados, set_de_tokens) sin acentos, en minúscula, ≥2 chars.
    Sin punto final (para tolerar 'S.' como 's'), y sin el punto que separa iniciales.
    """
    if not n:
        return [], set()
    n = strip_accents(n).lower()
    # separar tokens y quitar signos
    parts = re.split(r"[\s,]+", n)
    tokens = []
    for p in parts:
        p = re.sub(r"[^a-z]", "", p)  # solo letras
        if len(p) >= 2:
            tokens.append(p)
    return tokens, set(tokens)

def levenshtein(a: str, b: str, cap: int = 3) -> int:
    """Distancia con early-exit al superar cap."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        min_row = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(cur[-1] + 1, prev[j] + 1, prev[j-1] + cost)
            cur.append(v)
            if v < min_row:
                min_row = v
        if min_row > cap:
            return cap + 1
        prev = cur
    return prev[-1]

def name_ratio(a_tokens: list[str], b_tokens: list[str]) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    return SequenceMatcher(None, " ".join(sorted(a_tokens)), " ".join(sorted(b_tokens))).ratio()

# ------------------------------------------------------------------ load boom
print("[match] cargando boom_users...", file=sys.stderr)
boom_by_email: dict[str, list[dict]] = defaultdict(list)
boom_by_phone: dict[str, list[dict]] = defaultdict(list)
boom_by_last: dict[str, list[dict]] = defaultdict(list)
boom_by_first: dict[str, list[dict]] = defaultdict(list)
boom_by_local: dict[str, list[dict]] = defaultdict(list)
boom_users: list[dict] = []

with (RAW / "boom_users.csv").open() as f:
    for r in csv.DictReader(f):
        e = norm_email(r["email"])
        p = norm_phone(r["phone"])
        first_tokens, first_set = norm_name(r["first_name"])
        last_tokens, last_set = norm_name(r["last_name"])
        all_tokens = first_tokens + last_tokens
        all_set = first_set | last_set
        u = {
            "id": r["boom_user_id"],
            "email": e,
            "email_local": email_local(e),
            "email_domain": email_domain(e),
            "phone": p,
            "first_tokens": first_tokens,
            "last_tokens": last_tokens,
            "all_tokens": all_tokens,
            "all_set": all_set,
            "city": strip_accents(r.get("city", "")).lower(),
        }
        boom_users.append(u)
        if e:
            boom_by_email[e].append(u)
        if p:
            boom_by_phone[p].append(u)
        if u["email_local"]:
            boom_by_local[u["email_local"]].append(u)
        for t in last_tokens:
            boom_by_last[t].append(u)
        for t in first_tokens:
            boom_by_first[t].append(u)
print(f"[match] {len(boom_users)} boom users cargados", file=sys.stderr)

# ------------------------------------------------------------------ score sale vs user
def score(sale: dict, user: dict) -> float:
    # 1.00 — email exacto normalizado
    if sale["email"] and user["email"] and sale["email"] == user["email"]:
        return 1.00
    # phone exacto
    phone_match = bool(sale["phone"] and user["phone"] and sale["phone"] == user["phone"])
    name_overlap = (sale["all_set"] & user["all_set"])
    last_hit = bool(set(sale["last_tokens"]) & set(user["last_tokens"]))
    first_hit = bool(set(sale["first_tokens"]) & set(user["first_tokens"]))
    # 0.95 — phone exacto + nombre coincide en apellido o nombre
    if phone_match and (last_hit or first_hit):
        return 0.95
    # 0.90 — email local coincide + dominio Levenshtein ≤2, Y token_set con Jaccard ≥ 0.5
    if sale["email_local"] and user["email_local"] and sale["email_local"] == user["email_local"]:
        if sale["email_domain"] and user["email_domain"]:
            if levenshtein(sale["email_domain"], user["email_domain"], 2) <= 2:
                if sale["all_set"] and user["all_set"]:
                    jacc = len(name_overlap) / len(sale["all_set"] | user["all_set"])
                    if jacc >= 0.5:
                        return 0.90
    # 0.85 — phone exacto sin confirmación de nombre
    if phone_match:
        return 0.85
    # 0.80 — nombre completo ratio ≥0.92 + city coincide + un canal parcial
    if sale["all_tokens"] and user["all_tokens"]:
        nr = name_ratio(sale["all_tokens"], user["all_tokens"])
        if nr >= 0.92 and sale["city"] and sale["city"] == user["city"]:
            partial_channel = False
            if sale["email_local"] and user["email_local"]:
                # local Levenshtein ≤2
                if levenshtein(sale["email_local"], user["email_local"], 2) <= 2:
                    partial_channel = True
            if not partial_channel and sale["phone"] and user["phone"]:
                # últimos 7 dígitos coinciden
                if sale["phone"][-7:] == user["phone"][-7:]:
                    partial_channel = True
            if partial_channel:
                return 0.80
    return 0.0

# ------------------------------------------------------------------ carga eventos para city
print("[match] cargando ft_events (city por event_id)...", file=sys.stderr)
event_city: dict[str, str] = {}
with (RAW / "ft_events.csv").open() as f:
    for r in csv.DictReader(f):
        event_city[r["event_id"]] = strip_accents(r.get("city", "")).lower()

# ------------------------------------------------------------------ iterate sales
print("[match] procesando ventas...", file=sys.stderr)
matches = []
stats = {"total": 0, "matched": 0, "ambiguous": 0}
by_score = defaultdict(int)

with (RAW / "ft_sales.csv").open() as f:
    for r in csv.DictReader(f):
        stats["total"] += 1
        e = norm_email(r["buyer_email"])
        p = norm_phone(r["buyer_phone"])
        first_tokens, first_set = norm_name(r["buyer_name"])
        # nombres FT no tienen first_name/last_name separado — todo va junto
        sale = {
            "id": r["sale_id"],
            "email": e,
            "email_local": email_local(e),
            "email_domain": email_domain(e),
            "phone": p,
            "first_tokens": first_tokens,
            "last_tokens": first_tokens,  # tratamos todo como candidato para ambos
            "all_tokens": first_tokens,
            "all_set": first_set,
            "city": event_city.get(r["event_id"], ""),
        }
        # candidatos por blocking
        cands: dict[str, dict] = {}
        if e and e in boom_by_email:
            for u in boom_by_email[e]:
                cands[u["id"]] = u
        if p and p in boom_by_phone:
            for u in boom_by_phone[p]:
                cands[u["id"]] = u
        if sale["email_local"] and sale["email_local"] in boom_by_local:
            for u in boom_by_local[sale["email_local"]]:
                cands[u["id"]] = u
        for t in first_tokens:
            for u in boom_by_last.get(t, []):
                cands[u["id"]] = u
            for u in boom_by_first.get(t, []):
                cands[u["id"]] = u
        # score
        scored = []
        for u in cands.values():
            s = score(sale, u)
            if s >= 0.80:
                scored.append((s, u["id"]))
        scored.sort(reverse=True)
        if not scored:
            continue
        top = scored[0]
        if len(scored) >= 2 and (top[0] - scored[1][0]) <= 0.05 and scored[1][0] >= 0.80 and top[1] != scored[1][1]:
            stats["ambiguous"] += 1
            continue
        matches.append((sale["id"], top[1], top[0]))
        stats["matched"] += 1
        by_score[round(top[0], 2)] += 1

# ------------------------------------------------------------------ write
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["sale_id", "boom_user_id", "confidence"])
    for m in matches:
        w.writerow([m[0], m[1], f"{m[2]:.2f}"])

print(f"[match] total={stats['total']} matched={stats['matched']} "
      f"ambiguous_descartado={stats['ambiguous']}", file=sys.stderr)
print("[match] por score:", dict(sorted(by_score.items(), reverse=True)), file=sys.stderr)
print(f"[match] escrito {OUT}", file=sys.stderr)
