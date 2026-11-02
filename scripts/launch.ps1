$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Test-DockerReady {
    $originalPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & docker info *> $null
        return ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $originalPreference }
}

try {
    Write-Host 'Yandex2Kindle: запускаем вашу библиотеку…'
    $dockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
    if (Test-Path $dockerBin) { $env:Path = "$dockerBin;$env:Path" }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Start-Process 'https://www.docker.com/products/docker-desktop/'
        throw 'Установите Docker Desktop с официальной страницы, завершите его настройку и запустите этот файл снова. Может потребоваться перезагрузка.'
    }
    if (-not (Test-DockerReady)) {
        $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (Test-Path $desktop) { Start-Process $desktop }
        Write-Host 'Ожидаем Docker Desktop. Если появилось окно настройки, завершите предложенные шаги…'
        $ready = $false
        for ($i = 0; $i -lt 90; $i++) {
            if (Test-DockerReady) { $ready = $true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $ready) { throw 'Docker ещё не готов. Завершите его настройку и запустите этот файл снова.' }
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Не найден Docker Compose. Обновите Docker Desktop.' }
    New-Item -ItemType Directory -Force -Path 'data','incoming' | Out-Null
    if (-not $env:HOST_IP) {
        $network = Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | Select-Object -First 1
        if ($network) { $env:HOST_IP = $network.IPv4Address.IPAddress | Select-Object -First 1 }
    }
    if (-not $env:APP_PORT -and (Test-Path 'data\launcher.port')) { $env:APP_PORT = (Get-Content 'data\launcher.port' -Raw).Trim() }
    if (-not $env:APP_PORT) { $env:APP_PORT = '8000' }
    $port = 0
    if (-not [int]::TryParse($env:APP_PORT, [ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw 'Неверный порт. Удалите data\launcher.port и повторите запуск.' }
    Write-Host 'Подготавливаем приложение. Первый запуск может занять несколько минут…'
    $started = $false
    for ($i = 0; $i -lt 10; $i++) {
        # Native stderr contains build progress as well as errors.
        $ErrorActionPreference = 'Continue'
        & docker compose up -d --build *> 'data\launcher.log'
        $result = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($result -eq 0) { $started = $true; break }
        if (-not (Select-String -Path 'data\launcher.log' -Pattern 'already allocated|address already in use|ports are not available' -Quiet)) { break }
        $port++; $env:APP_PORT = [string]$port
    }
    if (-not $started) { throw 'Не удалось запустить библиотеку. Проверьте Docker и интернет. Подробности: data\launcher.log' }
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'data\launcher.port'), [string]$port)
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:$port/health" -TimeoutSec 3
            if ($health.ok) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'Приложение пока не ответило. Подождите немного и повторите запуск.' }
    $siteUrl = (& docker compose exec -T yandex2kindle python -m app.setup --url "http://localhost:$port" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $siteUrl.StartsWith("http://localhost:$port/")) { throw 'Не удалось открыть первоначальную настройку.' }
    Write-Host 'Готово. Открываем библиотеку в браузере.'
    Start-Process $siteUrl
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'Инструкция: НАЧНИТЕ ЗДЕСЬ.html'
    exit 1
}
