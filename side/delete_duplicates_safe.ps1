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

function Get-NormalizedHash {
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
    'STATUS.txt'
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

        $minPathLength = ($dupes | ForEach-Object { $_.FullName.Length } | Measure-Object -Minimum).Minimum
        $keeper = $dupes | Where-Object { $_.FullName.Length -eq $minPathLength } | Select-Object -First 1
        $toRemove = $dupes | Where-Object { $_.FullName.Length -gt $minPathLength }

        if ($toRemove.Count -eq 0) {
            Write-Host "Дубликаты ($($dupes[0].Length) байт): пути одинаковой длины — пропущено ($($dupes.Count) шт.)" -ForegroundColor DarkGray
            foreach ($d in $dupes) { Write-Host "  [SKIP] $($d.FullName)" -ForegroundColor DarkGray }
            continue
        }

        $shortHash = ($key -split '\|')[-1].Substring(0, 16)
        Write-Host "Дубликаты ($($dupes[0].Length) байт, SHA-256: $shortHash...): оставить '$($keeper.Name)' | удалить $($toRemove.Count)" -ForegroundColor Yellow
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