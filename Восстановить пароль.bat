@echo off
cd /d "%~dp0"
set "PATH=%PATH%;%ProgramFiles%\Docker\Docker\resources\bin"
docker compose exec yandex2kindle python -m app.auth --reset-owner
pause
