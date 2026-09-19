param(
    [string]$RootPath = "C:\",
    [int]$TopLevel = 20,
    [int]$DrillDownDepth = 2,
    [float]$MinGB = -1,
    [float]$AbsoluteMinGB = 0.5,
    [switch]$ExcludeWindowsMain = $true   # По умолчанию исключаем основное тело Windows
)

# Подпапки внутри C:\Windows, которые мы всё же учитываем (остальное игнорируем)
$WindowsIncludeSubdirs = @(
    'Temp',
    'Prefetch',
    'SoftwareDistribution\Download'
)

# Функция рекурсивного подсчёта размера (обычная)
function Get-SizeRecursive {
    param($Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return (Get-Item -LiteralPath $Path -Force).Length
    }
    $total = 0L
    $items = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue
    foreach ($f in $items) {
        $total += $f.Length
    }
    return $total
}

# Функция специального подсчёта размера папки Windows (только безопасные для очистки части)
function Get-WindowsSizeSpecial {
    param($WindowsPath)
    $total = 0L

    # 1. Файлы в корне C:\Windows (например, логи, .exe, .dll)
    $filesInRoot = Get-ChildItem -LiteralPath $WindowsPath -File -Force -ErrorAction SilentlyContinue
    $total += ($filesInRoot | Measure-Object -Property Length -Sum).Sum

    # 2. Только указанные подпапки (рекурсивно)
    foreach ($sub in $WindowsIncludeSubdirs) {
        $subPath = Join-Path $WindowsPath $sub
        if (Test-Path -LiteralPath $subPath) {
            $total += Get-SizeRecursive -Path $subPath
        }
    }

    return $total
}

# Получаем все элементы корня
$rootItems = @(Get-ChildItem -LiteralPath $RootPath -Force -ErrorAction SilentlyContinue)
$results = @()
$totalRoot = $rootItems.Count
$current = 0
$totalUsedBytes = 0L

Write-Host "`nСканирование корневых папок и файлов $RootPath ..." -ForegroundColor Cyan
foreach ($item in $rootItems) {
    $current++
    $percent = [math]::Round(($current / $totalRoot) * 100)
    Write-Progress -Activity "Анализ $($item.Name)" -Status "$current из $totalRoot" -PercentComplete $percent

    # Специальная обработка для C:\Windows
    if ($ExcludeWindowsMain -and $item.PSIsContainer -and $item.Name -eq 'Windows' -and $item.FullName -eq "$RootPath\Windows") {
        $sizeBytes = Get-WindowsSizeSpecial -WindowsPath $item.FullName
    } else {
        $sizeBytes = Get-SizeRecursive -Path $item.FullName
    }

    $totalUsedBytes += $sizeBytes
    $results += [PSCustomObject]@{
        Name      = $item.Name
        FullPath  = $item.FullName
        Type      = if ($item.PSIsContainer) { "DIR" } else { "FILE" }
        SizeBytes = $sizeBytes
        SizeGB    = [math]::Round($sizeBytes / 1GB, 3)
    }
}
Write-Progress -Activity "Анализ" -Completed

# Общий занятый объём (с нашей "урезанной" оценкой Windows)
$totalUsedGB = [math]::Round($totalUsedBytes / 1GB, 2)
Write-Host "`nОбщий занятый объём на диске (оценка с исключением системных файлов Windows): $totalUsedGB ГБ" -ForegroundColor Cyan

# Автоматическое определение порога MinGB
if ($MinGB -lt 0) {
    $autoMinGB = [math]::Round($totalUsedGB / $TopLevel, 2)
    $MinGB = [math]::Max($autoMinGB, $AbsoluteMinGB)
    Write-Host "Автоматически установлен порог углубления: $MinGB ГБ" -ForegroundColor Yellow
} else {
    Write-Host "Используется заданный порог углубления: $MinGB ГБ" -ForegroundColor Yellow
}

# Сортировка и отбор топ-уровня
$topResults = $results | Sort-Object -Property SizeBytes -Descending | Select-Object -First $TopLevel

Write-Host "`n=== ТОП-$TopLevel ЭЛЕМЕНТОВ В $RootPath ===" -ForegroundColor Green
$topResults | Format-Table -Property Type, Name, SizeGB -AutoSize

# Функция для углублённого анализа (обычная, без исключений Windows, так как вглубь мы идём только по крупным папкам, которые не Windows)
function DrillDown {
    param($Path, $Depth, $Indent)
    if ($Depth -le 0) { return }
    $items = Get-ChildItem -LiteralPath $Path -Force -Directory -ErrorAction SilentlyContinue
    $subResults = @()
    foreach ($sub in $items) {
        # Если мы внутри Windows и включено исключение, используем специальную функцию для подпапок
        if ($ExcludeWindowsMain -and $Path -like "$RootPath\Windows*") {
            # Определяем, является ли текущая подпапка одной из разрешённых
            $relativePath = $sub.FullName.Substring($RootPath.Length).TrimStart('\')
            $allowed = $false
            foreach ($inc in $WindowsIncludeSubdirs) {
                if ($relativePath -eq "Windows\$inc" -or $relativePath.StartsWith("Windows\$inc\")) {
                    $allowed = $true
                    break
                }
            }
            if (-not $allowed) {
                # Пропускаем неразрешённые подпапки Windows
                continue
            }
        }
        $size = Get-SizeRecursive -Path $sub.FullName
        $subResults += [PSCustomObject]@{
            Name   = $sub.Name
            SizeGB = [math]::Round($size / 1GB, 3)
        }
    }
    $topSub = $subResults | Sort-Object -Property SizeGB -Descending | Where-Object { $_.SizeGB -ge $MinGB } | Select-Object -First 5
    foreach ($s in $topSub) {
        Write-Host "$Indent|-- $($s.SizeGB) ГБ  $($s.Name)" -ForegroundColor Yellow
        DrillDown -Path (Join-Path $Path $s.Name) -Depth ($Depth - 1) -Indent "$Indent    "
    }
}

Write-Host "`n=== УГЛУБЛЁННЫЙ АНАЛИЗ (папки > $MinGB ГБ, глубина $DrillDownDepth) ===" -ForegroundColor Magenta
foreach ($item in $topResults | Where-Object { $_.Type -eq "DIR" -and $_.SizeGB -ge $MinGB }) {
    Write-Host "`n$($item.SizeGB) ГБ  $($item.FullPath)" -ForegroundColor Cyan
    DrillDown -Path $item.FullPath -Depth $DrillDownDepth -Indent "    "
}