<#
.SYNOPSIS
Удаляет дубликаты, оставляя файлы с кратчайшим путём среди всех указанных корневых папок.
Поддерживает параметр -Path. Оптимизирован: сначала группирует по размеру, затем хэширует только кандидатов.
Добавлен прогресс-бар.
#>
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='Medium')]
param(
    [Parameter(Mandatory=$false)]
    [string[]]$Path = @("E:\Jericho")
)

# --- Кэш SHA-256 ---
$cachePath = Join-Path $PSScriptRoot ".sha256cache.json"
$global:hashCache = @{}
if (Test-Path $cachePath) {
    try {
        $cacheData = Get-Content $cachePath -Raw | ConvertFrom-Json
        foreach ($prop in $cacheData.psobject.properties) {
            $global:hashCache[$prop.Name] = $prop.Value
        }
    } catch {}
}
$global:cacheChanged = $false

function Get-NormalizedHash {
    param([string]$FilePath)
    try {
        $fi = Get-Item -LiteralPath $FilePath -ErrorAction Stop
        $cacheKey = "$FilePath|$($fi.Length)|$($fi.LastWriteTimeUtc.Ticks)"
        if ($global:hashCache.ContainsKey($cacheKey)) {
            return $global:hashCache[$cacheKey]
        }
    } catch {
        $cacheKey = $null
    }

    $h = Get-FileHashInternal -FilePath $FilePath
    if ($h -and $cacheKey) {
        $global:hashCache[$cacheKey] = $h
        $global:cacheChanged = $true
    }
    return $h
}

function Get-FileHashInternal {
    param([string]$FilePath)
    $ext = [System.IO.Path]::GetExtension($FilePath).ToLower()
    $textExts = @('.txt','.md','.ps1','.py','.json','.xml','.csv','.yaml','.yml','.cfg','.ini','.log','.lacuna','.html','.css','.js')
    if ($ext -in $textExts) {
        try {
            $content = [System.IO.File]::ReadAllText($FilePath, [System.Text.Encoding]::UTF8)
            $content = $content -replace "`r`n", "`n" -replace "`r", "`n"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
            $sha = [System.Security.Cryptography.SHA256]::Create()
            $hashBytes = $sha.ComputeHash($bytes)
            $sha.Dispose()
            return -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
        } catch {
            Write-Warning "Fallback to raw hash for '$FilePath': $_"
        }
    }
    $stream = [System.IO.File]::OpenRead($FilePath)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $hashBytes = $sha.ComputeHash($stream)
        $sha.Dispose()
        return -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
    } finally { $stream.Dispose() }
}

function Test-ContentEquality {
    param([string]$RefPath, [string]$DiffPath)
    $ext = [System.IO.Path]::GetExtension($RefPath).ToLower()
    $textExts = @('.txt','.md','.ps1','.py','.json','.xml','.csv','.yaml','.yml','.cfg','.ini','.log','.lacuna','.html','.css','.js')
    if ($ext -notin $textExts) { return $true }

    try {
        $refText = [System.IO.File]::ReadAllText($RefPath, [System.Text.Encoding]::UTF8)
        $diffText = [System.IO.File]::ReadAllText($DiffPath, [System.Text.Encoding]::UTF8)
        $refLines = ($refText -replace "`r`n", "`n" -replace "`r", "`n").Split("`n", [StringSplitOptions]::RemoveEmptyEntries)
        $diffLines = ($diffText -replace "`r`n", "`n" -replace "`r", "`n").Split("`n", [StringSplitOptions]::RemoveEmptyEntries)
        $diff = Compare-Object -ReferenceObject $refLines -DifferenceObject $diffLines
        return -not $diff
    } catch {
        Write-Warning "Error second check '$DiffPath': $_"
        return $false
    }
}

# ----- Main logic -----
Write-Host "Сканирование..." -ForegroundColor Cyan

# Собираем все файлы сразу, используя быстрый Get-ChildItem (без медленного массива)
# --- Исключения: файлы, которые НЕ дедуплицировать ---
# (даже если содержимое совпадает — это разные контексты)
$EXCLUDE_NAMES = @(
    # Пустые файлы-маркеры в профилях агентов (у каждого свои, не дубли)
    'reminders.json',
    'processed_inbox.json',
    '.gitkeep',
    # Ключи
    'private_key.pem',
    'public_key.pem',
    # Git-служебные
    'HEAD', 'description', 'exclude', 'config', 'index',
    'packed-refs', 'ORIG_HEAD', 'FETCH_HEAD', 'MERGE_HEAD',
    # Readme-заглушки и STATUS-маркеры (даже если содержание совпадает —
    # нужны как описания проектов в своих папках)
    'Readme.txt',
    'STATUS.txt',
    # Python-пакеты: пустой __init__.py в сотнях пакетов — не дубль
    '__init__.py',
    '__main__.py',
    'conftest.py',
    'setup.py',
    'setup.cfg',
    'pyproject.toml',
    'Pipfile',
    'Pipfile.lock',
    'poetry.lock',
    'MANIFEST.in',
    'requirements.txt',
    'requirements-dev.txt',
    'requirements-test.txt',
    'tox.ini',
    'pytest.ini',
    'alembic.ini',
    # Node/JS-проекты
    'package.json',
    'package-lock.json',
    'tsconfig.json',
    '.eslintrc',
    '.eslintrc.json',
    '.prettierrc',
    '.prettierrc.json',
    '.babelrc',
    # Git
    '.gitignore',
    '.gitattributes',
    '.gitmodules',
    # Environment
    '.env',
    '.env.example',
    '.env.local',
    '.env.production',
    '.env.development',
    '.python-version',
    '.nvmrc',
    '.ruby-version',
    # Docker / CI
    'Dockerfile',
    'docker-compose.yml',
    'docker-compose.yaml',
    '.dockerignore',
    # Build-системы
    'Makefile',
    'CMakeLists.txt',
    'build.gradle',
    'pom.xml',
    # Лицензии (могут совпадать в разных проектах)
    'LICENSE',
    'LICENSE.txt',
    'LICENSE.md',
    'LICENSE.rst',
    'COPYING',
    'COPYING.txt',
    # Прочие служебные
    '.editorconfig',
    '.htaccess',
    '.htpasswd',
    'robots.txt',
    '.flake8',
    '.pylintrc',
    '.isort.cfg',
    'Cargo.toml',
    'Cargo.lock',
    'go.mod',
    'go.sum',
    'Gemfile',
    'Gemfile.lock'
)

$EXCLUDE_REGEX = @(
    # reminders/processed_inbox у ЛЮБОГО агента в profiles
    '\\profiles\\[^\\]+\\memory\\(reminders|processed_inbox)\.json$',
    # git хуки-шаблоны (восстанавливаются через git init)
    '\\.git\\hooks\\.*\.sample$',
    # git-служебные пути
    '\\.git\\(HEAD|description|info\\exclude|config|packed-refs)$',
    '\\.git\\logs\\',
    '\\.git\\refs\\',
    # Наши .bak_* файлы (это мы их наделали)
    '\.bak_[a-z0-9_]+$',
    # Конфиги по префиксу: config.*, settings.*, params.*, conf.* —
    # в любой папке (у каждого проекта свой конфиг, даже если совпадает)
    '(?i)(^|[\\/])(config|settings|params|conf|options|preferences)\.[a-z0-9]+$',
    # Скрытые служебные файлы в любой папке (.env*, .git*, .dockerignore,
    # .editorconfig и т.п.) — их много, имена разные, но не дубли
    '(?i)(^|[\\/])\.[a-z][a-z0-9_.-]*$',
    # llama-server бинарники (одинаковый бинарь, разные имена)
    'llama-(llava|gemma3|minicpmv)-cli\.exe$',
    # etalons между private и public — уже junction, не трогать
    '\\etalons\\',
    # sim3d	exture_* (могут зваться по имени)
    '\\sim3d\\data\\texture_',
    # Материалы исследования субъектности — в tasks и research разные
    '\\tasks\\Исследование субъектности',
    # Core_Agents_from_public — копии агентов (разные контексты)
    '\\Core_Agents_from_public\\'

    # Защита контекстов — не удалять копии отсюда
    '\\public\\',
    '\\profiles\\',
    '\\_from_public\\',
    '\\_from_private\\',
    '\\Personal\\',
    '\\research\\',
    '\\tasks\\PsychoHistory\\',
    '\\tasks\\Lacunas\\',
    '\\tasks\\Monitoring\\',
    '\\Сенсорика\\',
    '\\Agents\\core\\00_core_from_private\\',
    '\\docs\\v4pro\\',
    '\\analysis\\fact_checks\\',
    '\\Private\\protocols\\'

)

function Test-Excluded($filePath) {
    $name = [System.IO.Path]::GetFileName($filePath)
    if ($name -in $EXCLUDE_NAMES) { return $true }
    foreach ($rx in $EXCLUDE_REGEX) {
        if ($filePath -match $rx) { return $true }
    }
    return $false
}

$allFiles = foreach ($root in $Path) {
    if (Test-Path $root -PathType Container) {
        Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Attributes -notmatch 'ReparsePoint' -and
                -not (Test-Excluded $_.FullName)
            }
    }
}

if (-not $allFiles) {
    Write-Host "Файлы не найдены." -ForegroundColor Yellow
    exit
}

Write-Host "Всего файлов: $($allFiles.Count)" -ForegroundColor Cyan

# 1. Группируем по размеру (мгновенно)
Write-Host "Группировка по размеру..." -ForegroundColor Cyan
$bySize = $allFiles | Group-Object Length | Where-Object { $_.Count -gt 1 }
Write-Host "Групп с одинаковым размером: $($bySize.Count)" -ForegroundColor Cyan

$totalRemoved = 0
$totalGroups = 0
$processedGroups = 0

# 2. Хэшируем только кандидатов, с прогресс-баром
foreach ($sizeGroup in $bySize) {
    $processedGroups++
    $percent = [int]($processedGroups / $bySize.Count * 100)
    Write-Progress -Activity "Поиск дубликатов" -Status "Обработано групп: $processedGroups из $($bySize.Count)" -PercentComplete $percent -CurrentOperation "Размер: $($sizeGroup.Name) байт"

    $files = $sizeGroup.Group
    $hashGroups = @{}
    foreach ($file in $files) {
        try {
            $hash = Get-NormalizedHash -FilePath $file.FullName
            $key = "$($file.Length)|$hash"
            if (-not $hashGroups.ContainsKey($key)) { $hashGroups[$key] = @() }
            $hashGroups[$key] += $file
        } catch {
            Write-Warning "Skip '$($file.FullName)': $_"
        }
    }

    foreach ($key in $hashGroups.Keys) {
        $dupes = $hashGroups[$key]
        if ($dupes.Count -le 1) { continue }
        $totalGroups++

        # Разделяем на живые (не архив) и архивные. Keeper — ВСЕГДА живой.
        $archiveRx = '(?i)[\\/](archive|BACKUPS|_tmp_|early_history_AGI|Jericho_Backup_|Jericho_archeology_)[\\/]'
        $live = @($dupes | Where-Object { $_.FullName -notmatch $archiveRx })
        $archived = @($dupes | Where-Object { $_.FullName -match $archiveRx })

        if ($live.Count -eq 0) {
            Write-Host "Дубликаты ($($dupes[0].Length) байт): все копии в архиве — пропущено ($($dupes.Count) шт.)" -ForegroundColor DarkGray
            foreach ($d in $dupes) { Write-Host "  [SKIP-ARCHIVE] $($d.FullName)" -ForegroundColor DarkGray }
            continue
        }

        if ($live.Count -eq 1) {
            # Один живой + архивные: keeper — живой, архивные — удалить
            $keeper = $live[0]
            $toRemove = @($archived)
        } else {
            # Несколько живых: если разные длины путей — оставляем самый короткий
            $minLiveLength = ($live | ForEach-Object { $_.FullName.Length } | Measure-Object -Minimum).Minimum
            $liveMin = @($live | Where-Object { $_.FullName.Length -eq $minLiveLength })
            if ($liveMin.Count -gt 1) {
                Write-Host "Дубликаты ($($dupes[0].Length) байт): живые копии одинаковой длины — пропущено ($($live.Count) живых, $($archived.Count) архивных)" -ForegroundColor DarkGray
                foreach ($d in $dupes) { Write-Host "  [SKIP] $($d.FullName)" -ForegroundColor DarkGray }
                continue
            }
            $keeper = $liveMin[0]
            $toRemove = @($live | Where-Object { $_.FullName -cne $keeper.FullName }) + $archived
        }

        if ($toRemove.Count -eq 0) { continue }

        $shortHash = ($key -split '\|')[-1].Substring(0, 16)
        Write-Host "Дубликаты ($($dupes[0].Length) байт, SHA-256: $shortHash...): оставить '$($keeper.FullName)' | удалить $($toRemove.Count)" -ForegroundColor Yellow
        $ext = [System.IO.Path]::GetExtension($keeper.FullName).ToLower()
        $textExts = @('.txt','.md','.ps1','.py','.json','.xml','.csv','.yaml','.yml','.cfg','.ini','.log','.lacuna','.html','.css','.js')
        if ($ext -in $textExts) {
            Write-Host "  (Текст совпадает построчно. Если это разные версии (например, перевод) — значит, внутри идентичный текст.)" -ForegroundColor DarkYellow
        } else {
            Write-Host "  (Файлы бинарно идентичны, SHA-256 совпадает. Это точные копии.)" -ForegroundColor DarkYellow
        }
        Write-Host "  [KEEP] $($keeper.FullName)" -ForegroundColor Green

        foreach ($file in $toRemove) {
            if (-not (Test-ContentEquality -RefPath $keeper.FullName -DiffPath $file.FullName)) {
                Write-Host "  [SKIP] $($file.FullName) - различия (хэш совпал, текст нет)" -ForegroundColor DarkCyan
                continue
            }
            Write-Host "  [DEL]   $($file.FullName)" -ForegroundColor Red
            if ($PSCmdlet.ShouldProcess($file.FullName, "Удалить дубликат")) {
                try {
                    Remove-Item -Path $file.FullName -Force -ErrorAction Stop
                    $totalRemoved++
                } catch {
                    Write-Error "Не удалено '$($file.FullName)': $_"
                }
            }
        }
    }
}

Write-Progress -Activity "Поиск дубликатов" -Completed
Write-Host "Готово. Удалено: $totalRemoved (из $totalGroups групп дубликатов)" -ForegroundColor Cyan