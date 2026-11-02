#!/bin/sh
# Double-click entrypoint; never prints the bootstrap key or edits .env.
set -eu
cd "$(dirname "$0")/.."
PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"
export PATH
umask 077
open_url() {
    case "$(uname -s)" in
        Darwin) open "$1" ;;
        *) if command -v xdg-open >/dev/null 2>&1; then xdg-open "$1"; else printf 'Откройте библиотеку в браузере на этом компьютере.\n'; fi ;;
    esac
}
fail() {
    printf '\n%s\n' "$1"
    printf 'Подробности сохранены в data/launcher.log. Инструкция: НАЧНИТЕ ЗДЕСЬ.md\n'
    if [ -t 0 ]; then printf 'Нажмите Enter, чтобы закрыть окно…'; read -r ignored || true; fi
    exit 1
}
printf 'Yandex2Kindle — запускаем вашу библиотеку\n'
if ! command -v docker >/dev/null 2>&1; then
    printf 'Сначала установите Docker Desktop. Открываем официальную страницу.\n'
    open_url 'https://www.docker.com/products/docker-desktop/' || true
    fail 'После установки откройте Docker Desktop и снова запустите этот файл. На Windows может потребоваться перезагрузка.'
fi
if ! docker info >/dev/null 2>&1; then
    if [ "$(uname -s)" = 'Darwin' ]; then open -a Docker || true; fi
    printf 'Ожидаем запуска Docker Desktop. При первом запуске подтвердите его настройку в появившемся окне.\n'
    attempt=0
    until docker info >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        [ "$attempt" -lt 90 ] || fail 'Docker ещё не готов. Завершите его настройку, затем запустите библиотеку снова.'
        sleep 2
    done
fi
docker compose version >/dev/null 2>&1 || fail 'Не найден Docker Compose. Установите актуальный Docker Desktop.'
mkdir -p data incoming
if [ -z "${HOST_IP:-}" ]; then
    if [ "$(uname -s)" = 'Darwin' ]; then
        HOST_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
    elif command -v ip >/dev/null 2>&1; then
        HOST_IP=$(ip -4 route get 192.0.2.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}')
    fi
fi
export HOST_IP
if [ -z "${APP_PORT:-}" ] && [ -f data/launcher.port ]; then APP_PORT=$(cat data/launcher.port); fi
APP_PORT=${APP_PORT:-8000}
case "$APP_PORT" in *[!0-9]*|'') fail 'Неверный номер порта. Удалите data/launcher.port и повторите запуск.';; esac
export APP_PORT
printf 'Подготавливаем приложение. Первый запуск может занять несколько минут…\n'
attempt=0
until docker compose up -d --build >data/launcher.log 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 10 ] || ! grep -qiE 'already allocated|address already in use|ports are not available' data/launcher.log; then
        fail 'Не удалось запустить библиотеку. Проверьте, работает ли Docker и есть ли доступ к интернету.'
    fi
    APP_PORT=$((APP_PORT + 1)); export APP_PORT
done
printf '%s' "$APP_PORT" >data/launcher.port
attempt=0
until curl -fsS "http://127.0.0.1:$APP_PORT/health" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || fail 'Приложение ещё не ответило. Подождите немного и запустите файл снова.'
    sleep 2
done
site_url=$(docker compose exec -T yandex2kindle python -m app.setup --url "http://localhost:$APP_PORT") || fail 'Не удалось открыть начальную настройку.'
printf 'Готово. Открываем библиотеку в браузере.\n'
open_url "$site_url" || fail 'Откройте браузер и повторите запуск этого файла.'
