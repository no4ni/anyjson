# clone_anyjson.ps1 — клонирование anyjson в другое место
# =====================================================
# Параметры:
#   -Src <path>        Где лежит anyjson. По умолчанию: родитель $PSScriptRoot
#   -Dst <path>        Куда клонировать. Если не задан — спросит интерактивно.
#   -OldPath <path>    Старый путь anyjson для обновления config.json (иначе = $SRC)
#   -Force             Не спрашивать при перезаписи $Dst.
#   -WithKeys          Копировать api_keys.json как есть.
#   -SkipKeys          [по умолчанию] Не копировать ключи, положить заглушку.
#   -KeepBak           Копировать .bak_* и *.bak файлы (по умолчанию — нет).
#
# Примеры неинтерактивного запуска:
#   .\clone_anyjson.ps1 -Src "E:/Jericho/Private" -Dst "E:/Jericho/рабочее/anyjson" -Force
#   .\clone_anyjson.ps1 -Src "E:/Jericho/Private/anyjson" -Dst "E:/backup/anyjson" -Force -WithKeys

param(
    [string]$Src,
    [string]$Dst,
    [string]$OldPath,
    [switch]$Force,
    [switch]$WithKeys,
    [switch]$SkipKeys,
    [switch]$KeepBak
)

$ErrorActionPreference = 'Stop'

# ---- 1. Определяем источник ----
if ([string]::IsNullOrWhiteSpace($Src)) {
    $Src = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($Src)) {
        $Src = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
}

# Скрипт может лежать ВНУТРИ anyjson (тогда $Src\anyjson.py) либо в РОДИТЕЛЕ (тогда $Src\anyjson\anyjson.py)
$AnyjsonDir = $null
if (Test-Path (Join-Path $Src "anyjson.py")) {
    $AnyjsonDir = $Src
} elseif (Test-Path (Join-Path $Src "anyjson\anyjson.py")) {
    $AnyjsonDir = Join-Path $Src "anyjson"
} else {
    Write-Host "Не найден anyjson.py ни в $Src, ни в $Src\anyjson" -ForegroundColor Red
    exit 1
}

Write-Host "Источник anyjson: $AnyjsonDir" -ForegroundColor Cyan

# ---- 2. Определяем приёмник ----
if ([string]::IsNullOrWhiteSpace($Dst)) {
    Write-Host ""
    $Dst = (Read-Host "Куда клонировать (полный путь)").Trim()
    if ([string]::IsNullOrWhiteSpace($Dst)) {
        Write-Host "Путь не задан — отмена." -ForegroundColor Red
        exit 1
    }
}

if (Test-Path $Dst) {
    if ($Force) {
        Write-Host "Папка $Dst существует. -Force: перезаписываем." -ForegroundColor Yellow
    } else {
        Write-Host "Папка $Dst уже существует." -ForegroundColor Yellow
        $ans = (Read-Host "Перезаписать? (y/N)").Trim().ToLower()
        if ($ans -ne 'y') {
            Write-Host "Отменено." -ForegroundColor Red
            exit 0
        }
    }
} else {
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null
    Write-Host "Создана папка: $Dst" -ForegroundColor Green
}

if ([string]::IsNullOrWhiteSpace($OldPath)) {
    $OldPath = $AnyjsonDir
}

# ---- 3. Копирование ----
function ShouldSkip([string]$name) {
    if (-not $KeepBak) {
        if ($name -like "*.bak" -or $name -like "*.bak2" -or $name -like "*_bak_*" -or $name -like "*.bak_*") { return $true }
    }
    if ($name -eq "__pycache__") { return $true }
    return $false
}

# 3.1 корневые файлы
foreach ($f in @("anyjson.py", "Readme.txt", "clone_anyjson.ps1")) {
    $sp = Join-Path $AnyjsonDir $f
    if (Test-Path $sp) {
        Copy-Item $sp (Join-Path $Dst $f) -Force
        Write-Host "  $f"
    }
}

# 3.2 папки рекурсивно
foreach ($d in @("_core", "_actions", "side", "data", "proxy")) {
    $sp = Join-Path $AnyjsonDir $d
    if (-not (Test-Path $sp)) {
        New-Item -ItemType Directory -Path (Join-Path $Dst $d) -Force | Out-Null
        Write-Host "  $d\ (пустая)"
        continue
    }
    $dp = Join-Path $Dst $d
    New-Item -ItemType Directory -Path $dp -Force | Out-Null
    Get-ChildItem $sp -Recurse -Force | Where-Object {
        -not $_.PSIsContainer -and -not (ShouldSkip $_.Name)
    } | ForEach-Object {
        $rel = $_.FullName.Substring($sp.Length).TrimStart('\','/')
        $target = Join-Path $dp $rel
        $targetDir = Split-Path -Parent $target
        if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
        Copy-Item $_.FullName $target -Force
    }
    $cnt = (Get-ChildItem $dp -Recurse -File -Force | Measure-Object).Count
    Write-Host "  $d\ ($cnt файлов)"
}

# 3.3 profiles — всегда пустая
$new_profiles = Join-Path $Dst "profiles"
New-Item -ItemType Directory -Path $new_profiles -Force | Out-Null
Write-Host "  profiles\ (пустая — агенты заведутся заново)"

# ---- 4. Ключи ----
$new_keys = Join-Path $Dst "side\api_keys.json"
$old_keys = Join-Path $AnyjsonDir "side\api_keys.json"
$example_keys = Join-Path $AnyjsonDir "side\api_keys.example.json"

if ($WithKeys -and (Test-Path $old_keys)) {
    Copy-Item $old_keys $new_keys -Force
    Write-Host "  api_keys.json -> скопирован (по -WithKeys)" -ForegroundColor Yellow
} elseif (Test-Path $example_keys) {
    Copy-Item $example_keys $new_keys -Force
    Write-Host "  api_keys.json -> заглушка из example (ключи не копировались)" -ForegroundColor Gray
} else {
    Set-Content -Path $new_keys -Value '{}' -Encoding UTF8
    Write-Host "  api_keys.json -> пустой {}" -ForegroundColor Gray
}

# ---- 5. config.json — обновляем пути ----
$cfg_path = Join-Path $Dst "side\config.json"
if (Test-Path $cfg_path) {
    try {
        $cfg = Get-Content $cfg_path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.paths) {
            $cfg.paths.profiles_dir     = ($new_profiles -replace '\\','/')
            $cfg.paths.tools_dir        = "."
            $cfg.paths.public_tools_dir = ((Join-Path $Dst "side") -replace '\\','/')
            if (Test-Path (Join-Path $Dst "proxy")) {
                $cfg.paths.proxy_dir = ((Join-Path $Dst "proxy") -replace '\\','/')
            }
        }
        $cfg_json = $cfg | ConvertTo-Json -Depth 10
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($cfg_path, $cfg_json, $utf8)
        Write-Host "  side\config.json — пути обновлены" -ForegroundColor Cyan
    } catch {
        Write-Host "  config.json не парсится — оставляю как есть" -ForegroundColor Yellow
    }
}

# ---- 6. Итог ----
Write-Host ""
Write-Host "=== Готово ===" -ForegroundColor Green
Write-Host "Путь: $Dst" -ForegroundColor Cyan
Write-Host ""
Write-Host "Структура:" -ForegroundColor Yellow
Write-Host "  $Dst\anyjson.py          (точка входа)"
Write-Host "  $Dst\_core\"
Write-Host "  $Dst\_actions\"
Write-Host "  $Dst\side\config.json"
Write-Host "  $Dst\side\api_keys.json  ($(if ($WithKeys) {'скопирован'} else {'заглушка'}))"
Write-Host "  $Dst\data\"
Write-Host "  $Dst\profiles\           (пустая)"
Write-Host "  $Dst\proxy\"
Write-Host ""
Write-Host "Запуск:" -ForegroundColor Yellow
Write-Host "  py $Dst\anyjson.py <имя_агента>"
Write-Host ""