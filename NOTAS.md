# NOTAS — Hackathon FreeTicket

Entrega individual, 4h. Objetivo: cruce FT sale ↔ Boom user + proyección de
asistencia por evento de agosto.

## Qué asumí

- **La precisión del cruce vale más que la cobertura.** El evaluador dijo
  explícitamente que un falso match ensucia el forecast. Publico solo
  matches con confianza ≥ 0.80 y descarto ambigüedades (top1 - top2 ≤ 0.05
  con ambos ≥ 0.80). Prefiero un sale sin cruzar antes que uno mal cruzado.
- **Una parte grande de los compradores no existe en Boom.** El 36% que
  queda sin match no es un fallo — es la señal de que son nuevos, o son los
  casos de "email de la pareja" que no son cruzables por diseño.
- **`use_rate` crudo de Boom miente.** Mezcla membresía (techo ≤60%) con
  consumo mínimo (~75%). Lo calculo desagregado por tipo desde
  `boom_tickets.csv` y aplico los techos observados en julio.

## Jerarquía de matching (03_match.py)

Score por sale, se queda el mejor si no hay ambigüedad:

| Confianza | Regla |
|-----------|-------|
| **1.00**  | Email normalizado exacto. `DAVID LÓPEZ / gmial.com` → `david.lopez / gmail.com` cae aquí gracias a lowercase + fix de dominios comunes (`gmial→gmail`, `hotmial→hotmail`, `outlok→outlook`) + strip de `+alias`. |
| **0.95**  | Phone (últimos 10 dígitos) exacto **más** apellido o nombre coincide. Casos como `esteban.imenez81` con typo local, o `mariana.otiz` sin la r. |
| **0.90**  | Local del email idéntico **más** dominio con Levenshtein ≤2 **más** Jaccard ≥0.5 en tokens de nombre. |
| **0.85**  | Phone exacto sin confirmación de nombre. Riesgo del hermano; dejamos margen. |
| **0.80**  | `SequenceMatcher.ratio ≥ 0.92` sobre nombres normalizados + city coincide + un canal parcial (email local Lev ≤2 o últimos 7 dígitos del phone). |

**Normalización.** Email: lowercase, strip `+alias`, mapa de dominios típicos.
Phone: `re.sub(r"\D", "", p)[-10:]` colapsa los 5 formatos FT y también los
prefijos `+57`. Nombre: NFD sin acentos, lower, tokens ≥2 chars — tolera
apellido-primero (`Muñoz Isabella`) y solo-inicial (`S. Ramírez` → token `s`).

**Blocking.** Para cada sale, los candidatos vienen de: mismo email
normalizado, mismo phone, mismo local del email, o cualquier token de nombre
que exista en Boom. Esto evita el O(N×M) sin sacrificar cobertura.

**Umbral publicado: 0.80.** Todo lo demás no sale del archivo.

## Señales del forecast (04_forecast.py)

Itero **tickets** (no ventas — `qty > 1` es común). Cada ticket recibe una
probabilidad p_i, con la señal más específica disponible:

1. **Match Boom confiable** (score ≥ 0.80) → `use_rate` desagregado del user.
   Calculo `rate_membresia` (cap 0.60) y `rate_consumo_minimo` (cap 0.80) por
   separado y las pondero por cuántos tickets tiene de cada tipo.
2. **Sin match Boom** → prior por tipo FT: General/Preferencial/VIP = 0.94,
   Cortesía = 0.42.
3. **Ajuste por tipo de evento** (factor multiplicativo, clip a [0.05, 0.98]):
   - **Residencia con hermanos julio ≥100 tickets vendidos**: ancla en la tasa
     agregada de los hermanos, factor = `tasa_hermanos / 0.65`.
   - **Residencia con hermanos débiles**: mezcla 70% señal / 30% base.
   - **Fecha suelta con `attendance_rate_july` del artista**: factor suave
     bounded a [0.85, 1.15] hacia esa tasa.
   - **Nada**: sin factor.

## p10 / p90 honestos

Asistencia = Σ Bernoulli(p_i). Aproximación normal:
- `mean = Σ p_i`
- `var  = Σ p_i(1-p_i)`
- `σ_modelo = k · sqrt(var)` — `k` refleja incertidumbre del modelo,
  no solo del sampling:
  - k=1.0 residencia con hermanos fuertes (mejor señal disponible)
  - k=1.3 residencia débil
  - k=1.5 fecha suelta con julio del artista
  - k=1.8 fecha suelta sin nada
- `p10 = max(0, mean - 1.2816 · σ)`
- `p90 = min(tickets_sold, mean + 1.2816 · σ)`

Rango que va de 0 a `tickets_sold` no aparece — sería trampa; se penaliza,
no se premia.

## Números observados (baseline actual)

- Sales totales: 6.383 · matches ≥ 0.80: **4.039 (63%)** · ambigüedades
  descartadas: 31.
- Por score: 1.00 → 3.571 · 0.95 → 346 · 0.85 → 15 · 0.80 → 107.
- Spot-check manual (3/3 correctos): `DAVID LÓPEZ/gmial.com`,
  `esteban.imenez81` (typo local), `mariana.otiz` (typo local) — todos
  cruzados con el user Boom correcto.
- Eventos agosto: 30. Mix: **21 residencias fuertes**, 6 artistas con julio,
  3 fechas sueltas.

## Qué señal pesó más

**El tipo de ticket.** La diferencia 94% pagado vs 42% cortesía explica más
varianza en la asistencia que cualquier otra señal individual — y 41% de
los tickets de agosto son Cortesía. Sin ese quiebre, la proyección se
desvía por 20+ puntos porcentuales fácil. Boom afina el pronóstico
individual pero no cambia el orden de magnitud.

## Qué haría con 4 horas más

1. **Backtest sobre julio.** Correr el pipeline sobre los 32 eventos de
   julio como si no supiera los `checked_in`, comparar predicho vs real,
   calibrar `k_sigma` y factores empíricamente.
2. **Deduplicar personas en Boom.** Vi al menos un caso de duplicado
   probable durante EDA; consolidar antes del matching subiría la cobertura
   sin perder precisión.
3. **Regla de las 2 entradas máx.** Aplicar el cap v2 ("nadie más de 2
   entradas para el mismo evento") — hoy si alguien tiene 3+ con match
   Boom, cuento las 3 con la misma prob individual; debería atenuar la 3ª.
4. **Curva de llegada.** Con `checked_in_at` de julio → estimar distribución
   horaria por venue y armar el "link efímero" con personal sugerido en
   ventanas de 30 min. Es la única de las extras que dijeron.
