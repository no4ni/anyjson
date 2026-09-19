# clone_anyjson.ps1 — клонирование anyjson в другое место (полный переезд)
# profiles НЕ копируются — создаётся пустая папка.
# api_keys спрашиваются по каждому ключу.
# Запуск:  .\clone_anyjson.ps1

$ErrorActionPreference = "Stop"

# исходная папка = где лежит сам скрипт
$SRC = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SRC)) {
    $SRC = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not (Test-Path "$SRC\anyjson\anyjson.py")) {
    Write-Host "❌ В папке скрипта нет anyjson\anyjson.py: $SRC" -ForegroundColor Red
    Write-Host "   Скрипт должен лежать рядом с папкой anyjson\." -ForegroundColor Yellow
    exit 1
}
Write-Host "Источник: $SRC" -ForegroundColor Cyan

Write-Host ""
Write-Host "=== Клонирование anyjson (полный переезд) ===" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Куда клонировать ----
Write-Host "Куда клонировать (полный путь к НОВОЙ папке)?" -ForegroundColor Yellow
Write-Host "  Пример: H:\anyjson_clone" -ForegroundColor Gray
$DST = (Read-Host "> ").Trim().TrimEnd('\', '/')
if ([string]::IsNullOrWhiteSpace($DST)) {
    Write-Host "Отмена." -ForegroundColor Yellow
    exit 0
}
if (Test-Path $DST) {
    Write-Host "⚠️ Папка $DST уже существует." -ForegroundColor Yellow
    $ans = (Read-Host "Перезаписать? (y/N)").Trim().ToLower()
    if ($ans -ne 'y') {
        Write-Host "Отмена." -ForegroundColor Yellow
        exit 0
    }
} else {
    New-Item -ItemType Directory -Path $DST -Force | Out-Null
    Write-Host "✅ Создана папка: $DST" -ForegroundColor Green
}

# ---- 2. Копирование кода ----
Write-Host ""
Write-Host "=== Копирование кода ===" -ForegroundColor Cyan

# корневые файлы (anyjson.py внутри папки anyjson\)
foreach ($f in @("anyjson.py", "Readme.txt", "clone_anyjson.ps1")) {
    if (Test-Path "$SRC\$f") {
        Copy-Item "$SRC\$f" "$DST\$f" -Force
        Write-Host "  $f"
    }
}

# _core и _actions — рекурсивно
foreach ($d in @("_core", "_actions")) {
    if (Test-Path "$SRC\$d") {
        Copy-Item "$SRC\$d" "$DST\$d" -Recurse -Force
        Write-Host "  $d\"
    }
}

# side\
if (Test-Path "$SRC\side") {
    Copy-Item "$SRC\side" "$DST\side" -Recurse -Force
    Write-Host "  side\"
} else {
    New-Item -ItemType Directory -Path "$DST\side" -Force | Out-Null
}

# data\
if (Test-Path "$SRC\data") {
    Copy-Item "$SRC\data" "$DST\data" -Recurse -Force
    Write-Host "  data\"
} else {
    New-Item -ItemType Directory -Path "$DST\data" -Force | Out-Null
}

# ---- 3. Читаем config ----
$cfg = Get-Content "$SRC\side\config.json" -Raw -Encoding UTF8 | ConvertFrom-Json

# ---- 4. proxy ----
Write-Host ""
Write-Host "=== Proxy ===" -ForegroundColor Cyan

$old_proxy = $cfg.paths.proxy_dir
if ($old_proxy -and (Test-Path $old_proxy)) {
    $new_proxy = Join-Path $DST "proxy"
    Copy-Item $old_proxy $new_proxy -Recurse -Force
    $cfg.paths.proxy_dir = ($new_proxy -replace '\\','/')
    Write-Host "  proxy -> $new_proxy"
} else {
    $cfg.paths.proxy_dir = ($DST -replace '\\','/') + "/proxy"
    New-Item -ItemType Directory -Path $cfg.paths.proxy_dir -Force | Out-Null
    Write-Host "  proxy: создана пустая папка"
}

# ---- 5. api_keys — спрашиваем по каждому ключу ----
Write-Host ""
$old_keys = $cfg.paths.api_keys_file
$new_keys = Join-Path $DST "side\api_keys.json"
$cfg.paths.api_keys_file = ($new_keys -replace '\\','/')

if ($old_keys -and (Test-Path $old_keys)) {
    $kobj = $null
    try {
        $kobj = Get-Content $old_keys -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Host "  ⚠️ api_keys.json не парсится — копирую как есть" -ForegroundColor Yellow
        Copy-Item $old_keys $new_keys -Force
        $kobj = $null
    }

    if ($kobj) {
        Write-Host "=== API-ключи ===" -ForegroundColor Yellow
        Write-Host "  Enter=оставить, '-'=удалить, ввод=новый ключ" -ForegroundColor Gray
        Write-Host ""

        $new_kobj = New-Object PSObject
        foreach ($prop in $kobj.PSObject.Properties) {
            $name = $prop.Name
            $val = [string]$prop.Value
            $preview = if ($val.Length -gt 8) { $val.Substring(0,8) + "..." } else { $val }
            Write-Host ("  {0,-14} : {1}" -f $name, $preview) -ForegroundColor Gray
            $answer = (Read-Host ("    [{0}]" -f $name)).Trim()
            if ($answer -eq "-") {
                Write-Host "      → удалён" -ForegroundColor DarkYellow
                continue
            }
            if ([string]::IsNullOrWhiteSpace($answer)) {
                $new_kobj | Add-Member -NotePropertyName $name -NotePropertyValue $val -Force
            } else {
                $new_kobj | Add-Member -NotePropertyName $name -NotePropertyValue $answer -Force
                Write-Host "      → заменён" -ForegroundColor Green
            }
        }

        $new_kobj | ConvertTo-Json -Depth 10 | Set-Content $new_keys -Encoding UTF8
        Write-Host "  api_keys.json -> $new_keys" -ForegroundColor Cyan
    }
} else {
    Write-Host "  api_keys.json: файла нет — путь установлен на $new_keys" -ForegroundColor Yellow
}

# ---- 6. profiles — пустая папка ----
$new_profiles = Join-Path $DST "profiles"
New-Item -ItemType Directory -Path $new_profiles -Force | Out-Null
$cfg.paths.profiles_dir = ($new_profiles -replace '\\','/')
Write-Host "  profiles: создана пустая папка (данные не копировались)" -ForegroundColor Gray

# ---- 7. Обновляем paths ----
$cfg.paths.tools_dir = "."
$cfg.paths.public_tools_dir = ((Join-Path $DST "side") -replace '\\','/')

# ---- 8. Сохраняем config.json без BOM ----
$cfg_json = $cfg | ConvertTo-Json -Depth 10
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $DST "side\config.json"), $cfg_json, $utf8)

# ---- 9. Итог ----
Write-Host ""
Write-Host "=== Готово ===" -ForegroundColor Green
Write-Host ""
Write-Host "Путь: $DST" -ForegroundColor Cyan
Write-Host ""
Write-Host "Структура:" -ForegroundColor Yellow
Write-Host "  $DST\anyjson.py          (точка входа)"
Write-Host "  $DST\_core\              (ядро)"
Write-Host "  $DST\_actions\           (команды)"
Write-Host "  $DST\side\config.json    (конфиг)"
Write-Host "  $DST\side\api_keys.json  (ключи)"
Write-Host "  $DST\data\               (scripts_*.json)"
Write-Host "  $DST\profiles\           (пустая — агенты заведутся заново)"
Write-Host "  $DST\proxy\"
Write-Host ""
Write-Host "Запуск:" -ForegroundColor Yellow
Write-Host "  py $DST\anyjson.py Лира"
Write-Host ""