"""Standalone OAuth helper; requires only Python's standard library."""
import argparse
import getpass
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

# OAuth application used by the upstream yandex-book-api authentication flow:
# https://github.com/stepan163s/yandex-book-api/blob/main/yandex_book/auth.py
CLIENT_ID = "4483e97bab6e486a9822973109a14d05"
CALLBACK_HOST = "yx" + CLIENT_ID + ".oauth.yandex.ru"


def authorization_url(state):
    return "https://oauth.yandex.ru/authorize?" + urlencode({
        "client_id": CLIENT_ID, "response_type": "token", "state": state,
    })


def extract_token(callback, state):
    try:
        address = urlsplit(callback.strip())
        valid = (address.scheme == "https" and address.hostname == CALLBACK_HOST
                 and address.port in (None, 443) and address.username is None)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Вставьте полный HTTPS-адрес возврата Яндекса.")
    fields = {}
    for section in (address.query, address.fragment):
        for key, values in parse_qs(section, keep_blank_values=True).items():
            fields.setdefault(key, []).extend(values)
    states = fields.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0].encode(), state.encode()):
        raise ValueError("Откройте ссылку из текущего запуска: параметр state не совпал.")
    if "error" in fields:
        raise ValueError("Яндекс отклонил авторизацию. Повторите вход и разрешите доступ.")
    tokens = fields.get("access_token", [])
    if len(tokens) != 1 or not tokens[0] or any(char.isspace() for char in tokens[0]):
        raise ValueError("В адресе отсутствует однозначный access_token. Скопируйте адрес целиком.")
    return tokens[0]


def main():
    parser = argparse.ArgumentParser(description="Получение OAuth-токена Яндекс Книг без дополнительных пакетов")
    parser.add_argument("--manual", action="store_true", help="Ручной ввод адреса (используется по умолчанию)")
    parser.parse_args()
    state = secrets.token_urlsafe(32)
    print("Откройте ссылку в браузере и войдите в свой аккаунт Яндекса:\n")
    print(authorization_url(state), flush=True)
    print("\nПосле перенаправления скопируйте полный адрес из адресной строки.")
    print("Ввод скрыт: адрес содержит секретный токен. Не отправляйте его в чат.")
    try:
        while True:
            callback = getpass.getpass("Адрес возврата: ")
            try:
                token = extract_token(callback, state)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                continue
            print("\nВставьте следующую строку в .env (не публикуйте её):")
            print("YANDEX_BOOK_TOKEN=" + token)
            return 0
    except (EOFError, KeyboardInterrupt):
        print("\nАвторизация отменена.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
