#!/bin/sh
# Pass the computer's LAN address to Docker, never the container's bridge IP.
set -eu
cd "$(dirname "$0")/.."
if [ -z "${HOST_IP:-}" ]; then
    if command -v ipconfig >/dev/null 2>&1; then
        HOST_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
    elif command -v ip >/dev/null 2>&1; then
        HOST_IP=$(ip -4 route get 192.0.2.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')
    fi
fi
if [ -z "${HOST_IP:-}" ]; then
    echo 'Не удалось определить IP. Укажите адрес: HOST_IP=192.168.1.10 ./scripts/start.sh' >&2
    exit 1
fi
export HOST_IP
echo "IP компьютера: $HOST_IP"
echo "Библиотека: http://$HOST_IP:8000"
echo "Каталог для Kindle: http://$HOST_IP:8000/opds"
echo "Синхронизация KOReader: http://$HOST_IP:8000"
docker compose up -d --build
