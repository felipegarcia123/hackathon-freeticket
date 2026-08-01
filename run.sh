#!/usr/bin/env bash
# Un comando de punta a punta. Genera matches.csv y forecast.csv.
# Requiere: node (para npx), python3. Nada más.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .ft-hack.json ]; then
  echo "[run] no hay .ft-hack.json — corriendo setup..."
  echo "[run] usa: npx github:LucasLeguizamo/hackathon-freeticket setup TU-NOMBRE"
  exit 1
fi

if [ ! -f raw/ft_sales.csv ]; then
  echo "[run] jalando datos crudos..."
  bash scripts/01_pull.sh
else
  echo "[run] raw/ ya está poblado, se omite pull (borra raw/ para volver a bajar)"
fi

echo "[run] matching FT sales ↔ Boom users..."
python3 scripts/03_match.py

echo "[run] proyección de agosto..."
python3 scripts/04_forecast.py

echo ""
echo "[run] listo:"
echo "  matches.csv  ($(wc -l < matches.csv) líneas)"
echo "  forecast.csv ($(wc -l < forecast.csv) líneas)"
