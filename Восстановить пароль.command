#!/bin/sh
cd "$(dirname "$0")" || exit 1
PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"
export PATH
printf 'Восстановление пароля владельца. Книги сохранятся.\n'
docker compose exec yandex2kindle python -m app.auth --reset-owner
printf '\nНажмите Enter, чтобы закрыть окно…'
read -r ignored
