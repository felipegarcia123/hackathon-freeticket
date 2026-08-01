#!/usr/bin/env bash
# Baja los 7 recursos crudos a raw/. Idempotente: sobreescribe.
set -euo pipefail
cd "$(dirname "$0")/.."

FT="npx -y github:LucasLeguizamo/hackathon-freeticket"

echo "[pull] boom users..."
$FT pull boom users --out raw/boom_users.csv &
echo "[pull] boom profile..."
$FT pull boom profile --out raw/boom_profile.csv &
echo "[pull] boom tickets..."
$FT pull boom tickets --out raw/boom_tickets.csv &
echo "[pull] freeticket artists..."
$FT pull freeticket artists --out raw/ft_artists.csv &
echo "[pull] freeticket events..."
$FT pull freeticket events --out raw/ft_events.csv &
echo "[pull] freeticket sales..."
$FT pull freeticket sales --out raw/ft_sales.csv &
echo "[pull] freeticket tickets..."
$FT pull freeticket tickets --out raw/ft_tickets.csv &

wait
echo "[pull] listo. Conteo de filas:"
wc -l raw/*.csv
