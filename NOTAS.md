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

## p10 / p90 honestos: σ empírico, no a ojo

Asistencia = Σ Bernoulli(p_i). En vez de asumir un `k` a ojo:

1. **Backtest LOO sobre julio** (`scripts/backtest_julio.py`) — para cada
   evento julio, calibro `bias_type` y `σ_type` con los otros 31, luego
   predigo y comparo vs `checked_in_count` real.
2. En el forecast final aplico:
   - `mean_adj = Σ p_i + bias_type` (corrige sesgo residual del modelo)
   - `σ_use = max(σ_bernoulli, σ_type_residual)` (no subestimo la
     incertidumbre real observada)
   - `p10 = max(0, round(mean_adj - 1.2816 · σ_use))`
   - `p90 = min(tickets_sold, round(mean_adj + 1.2816 · σ_use))`

**Resultado del backtest LOO (32 eventos julio):**

| Tipo de evento       | n  | MAPE  | Cobertura p10-p90 | Bias medio |
|----------------------|----|-------|-------------------|------------|
| residencia_fuerte    | 23 | 15.3% | 78.3%             | +0.1       |
| artista_con_julio    | 9  |  2.4% | 88.9%             | -0.2       |
| **GLOBAL**           | 32 | **11.6%** | **81.2%**    | +0.0       |

Cobertura ~80% = rango honesto (por definición de intervalo del 80%).
Bias +0.0 = modelo calibrado. El rango `[0, tickets_sold]` no aparece.

## Números observados (versión final)

- Sales totales: 6.383 · matches ≥ 0.80: **4.039 (63%)** · ambigüedades
  descartadas: 31.
- Por score: 1.00 → 3.571 · 0.95 → 346 · 0.85 → 15 · 0.80 → 107.
- Spot-check manual (3/3 correctos): `DAVID LÓPEZ/gmial.com`,
  `esteban.imenez81` (typo local), `mariana.otiz` (typo local) — todos
  cruzados con el user Boom correcto.
- Eventos agosto: 30. Mix: **21 residencias fuertes**, 6 artistas con julio,
  3 fechas sueltas.
- Forecast: **3.903 asistentes esperados** en agosto sobre los 5.209
  tickets ya vendidos → 74.9% asistencia agregada. Rangos p10-p90 con
  ancho promedio 36 tickets (calibrado por σ empírico del backtest).

## Qué señal pesó más

**El tipo de ticket.** La diferencia 94% pagado vs 42% cortesía explica más
varianza en la asistencia que cualquier otra señal individual — y 47% de
los tickets de agosto son Cortesía. Sin ese quiebre, la proyección se
desvía por 20+ puntos porcentuales fácil. Boom afina el pronóstico
individual pero no cambia el orden de magnitud.

## El insight que nadie pidió: cortesías = plata dejada en la mesa

Combinando los dos entregables (matching → tipo → precio):

- **2.459 cortesías repartidas en agosto**, de las cuales solo **1.493
  van a usarse** (61%). El resto — **966 asientos** — van a estar vacíos.
- Precio promedio pagado de un ticket agosto = **$73.452 COP**.
- Costo de oportunidad = **~$71 millones COP en asientos-cortesía vacíos**.
  Ese es el precio real de la política actual de repartir cortesías.
- **Convertir el 30% de las cortesías más ineficientes** (las de canales
  ADMIN/RRPP en julio, que consistentemente entran menos) en tickets
  pagados con descuento del 30% recuperaría ~$21M COP.

El dashboard lo pone en el hero superior, con las palancas accionables al
lado. No es un nice-to-have: cambia cómo debería pensarse la política de
cortesías el lunes.

## Qué haría con 4 horas más

1. **Empujar la cobertura del matching sin bajar precisión.** Hoy 63% de
   sales van a `matches.csv`; sospecho que ~10 puntos más son alcanzables
   con: (a) coincidencia por token+ciudad+canal sin phone (regla 0.75),
   (b) fuzzy matching sobre `first_name+last_name` de Boom con nombre de
   FT invertido. Requiere validar el precision drop con un gold set.
2. **Regla de las 2 entradas máx (v2).** Si una persona (mismo boom_user)
   aparece con 3+ tickets para el mismo evento, atenuar la 3ª — hoy la
   cuento con la misma prob individual y sobreestimo un pico.
3. **Curva de llegada.** Con `checked_in_at` de julio, distribuir la
   asistencia esperada en ventanas de 30 min por venue, y construir el
   "link efímero" con personal sugerido — la extra que se pide.
4. **Feature: canal + anticipación.** `channel=RRPP` y compras del mismo
   día probablemente tienen assistance-rate distinta a `WEB` con 2
   semanas de anticipación. No lo modelé y podría cerrar el MAPE de 11%.
