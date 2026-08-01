# Hackathon FreeTicket — ¿Cuánta gente entra realmente?

Entrega del reto: cruzar compradores de FreeTicket con usuarios de Boom, y
proyectar asistencia para los 30 shows de agosto.

## Cómo correrlo

```bash
# 1. Token del hackathon (una sola vez)
npx github:LucasLeguizamo/hackathon-freeticket setup TU-NOMBRE

# 2. Todo el pipeline
bash run.sh
```

Genera `matches.csv` y `forecast.csv` en la raíz. Requiere `node` (para el CLI)
y `python3`. Cero dependencias Python extra — solo librería estándar.

## Salidas

- **`matches.csv`** — `sale_id, boom_user_id, confidence` con confianza ≥ 0.80.
  Las ambigüedades (dos candidatos empatados) se descartan a propósito: un
  falso match ensucia el forecast.
- **`forecast.csv`** — `event_id, expected_attendance, p10, p90` para los 30
  eventos de agosto, sobre los tickets ya adquiridos.

## Cómo se piensa

Ver `NOTAS.md` — asunciones, jerarquía de señales, umbrales, y qué haría con
cuatro horas más.

## Estructura

```
scripts/01_pull.sh      baja los 7 recursos crudos → raw/
scripts/03_match.py     cruce FT sale ↔ Boom user con confianza
scripts/04_forecast.py  proyección de agosto con p10/p90 honestos
run.sh                  comando único end-to-end
matches.csv             (generado)
forecast.csv            (generado)
NOTAS.md
```
