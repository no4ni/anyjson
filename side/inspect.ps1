<#
.SYNOPSIS
    Выбирает случайные строки из текстового файла, содержащие ключевые слова, с вероятностным проходом.
    Поддерживает вывод соседних строк (контекст) до и после каждого совпадения.
    Обновлённая версия: сначала собирает все совпадения, затем выбирает случайные из них по всему файлу.
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$FilePath,
    [Parameter(Mandatory=$true)]
    [string[]]$Keywords,
    [int]$TargetCount = 0,
    [double]$Probability = 0.5,
    [string]$Encoding = "utf8",
    [int]$Context = 0,

    [switch]$Regex
)
# Устанавливаем кодировку консоли для поддержки кириллицы
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
# Если TargetCount не задан, выбираем случайное от 3 до 10
if ($TargetCount -le 0) {
    $TargetCount = Get-Random -Minimum 3 -Maximum 11
}
# Проверка вероятности
if ($Probability -lt 0 -or $Probability -gt 1) {
    throw "Вероятность должна быть в диапазоне [0,1]"
}
# Функция нормализации пути с поддержкой кириллицы
function Get-NormalizedPath {
    param([string]$Path)
    
    # Убираем кавычки
    $Path = $Path.Trim('"', "'")
    
    # Расширяем переменные окружения
    $Path = [System.Environment]::ExpandEnvironmentVariables($Path)
    
    # Заменяем слэши
    $Path = $Path -replace '/', '\'
    
    # Убираем префикс \\?\ если он есть (он вызывает проблемы с кириллицей)
    if ($Path -like '\\?\*') {
        $Path = $Path.Substring(4)
    }
    
    # Если путь относительный, делаем абсолютным
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        $Path = Join-Path (Get-Location).Path $Path
    }
    
    return $Path
}
# Нормализация пути
$FilePath = Get-NormalizedPath -Path $FilePath
# Проверка существования файла с поддержкой кириллицы
try {
    # Пробуем найти файл напрямую
    if (Test-Path -LiteralPath $FilePath -PathType Leaf) {
        $fileInfo = Get-Item -LiteralPath $FilePath
    }
    else {
        # Пробуем найти файл с учетом регистра
        $directory = Split-Path $FilePath -Parent
        $fileName = Split-Path $FilePath -Leaf
        
        if (Test-Path -LiteralPath $directory) {
            # Ищем файл с игнорированием регистра
            $foundFile = Get-ChildItem -Path $directory -Filter $fileName -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($foundFile) {
                $fileInfo = $foundFile
                $FilePath = $foundFile.FullName
            }
        }
    }
    
    if (-not $fileInfo) {
        throw "Файл не найден: $FilePath"
    }
}
catch {
    Write-Error "Ошибка при проверке файла: $_"
    exit 1
}
# Определение кодировки
$encodingObj = switch ($Encoding.ToLower()) {
    "utf8"      { [System.Text.Encoding]::UTF8 }
    "utf8bom"   { [System.Text.UTF8Encoding]::new($true) }
    "unicode"   { [System.Text.Encoding]::Unicode }
    "bigendianunicode" { [System.Text.Encoding]::BigEndianUnicode }
    "ascii"     { [System.Text.Encoding]::ASCII }
    "windows1251" { [System.Text.Encoding]::GetEncoding(1251) }
    "cp866"     { [System.Text.Encoding]::GetEncoding(866) }
    default     { [System.Text.Encoding]::UTF8 }
}
# Функция проверки наличия ключевого слова (регистронезависимо)
function Test-StringContainsKeyword {
    param([string]$Line, [string[]]$Keywords, [switch]$Regex)
    
    if ([string]::IsNullOrEmpty($Line)) {
        return $false
    }
    
    foreach ($kw in $Keywords) {
        if ([string]::IsNullOrEmpty($kw)) {
            continue
        }
        if ($Regex) {
            try {
                if ([regex]::IsMatch($Line, $kw, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
                    return $true
                }
            } catch {
                continue
            }
        } else {
            if ($Line.IndexOf($kw, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $true
            }
        }
    }
    return $false
}
# Чтение файла
$allLines = [System.Collections.Generic.List[string]]::new()
try {
    # Создаем StreamReader с правильной кодировкой
    $reader = [System.IO.StreamReader]::new($FilePath, $encodingObj)
    try {
        while (($line = $reader.ReadLine()) -ne $null) {
            $allLines.Add($line)
        }
    }
    finally {
        # Закрываем и освобождаем ресурсы
        if ($reader -ne $null) {
            $reader.Close()
            $reader.Dispose()
        }
    }
}
catch {
    Write-Error "Ошибка при чтении файла: $_"
    Write-Host "`nПопробуйте указать правильную кодировку:" -ForegroundColor Yellow
    Write-Host "  -Encoding windows1251 - для файлов в кодировке Windows" -ForegroundColor Yellow
    Write-Host "  -Encoding utf8 - для файлов в UTF-8" -ForegroundColor Yellow
    Write-Host "  -Encoding cp866 - для файлов в DOS кодировке" -ForegroundColor Yellow
    exit 1
}
$totalLines = $allLines.Count
if ($totalLines -eq 0) {
    Write-Warning "Файл пуст или не содержит читаемых строк."
    exit
}
# Собираем все индексы строк, содержащих ключевые слова
$matchIndices = [System.Collections.Generic.List[int]]::new()
for ($i = 0; $i -lt $totalLines; $i++) {
    $line = $allLines[$i]
    if (Test-StringContainsKeyword -Line $line -Keywords $Keywords -Regex:$Regex) {
        $matchIndices.Add($i)
    }
}
$foundCount = $matchIndices.Count
if ($foundCount -eq 0) {
    Write-Host "В $FilePath не нашлось: $($Keywords -join ', ')" -ForegroundColor Yellow
    exit
}
# Выбираем случайные индексы из всех совпадений, если их больше TargetCount
if ($foundCount -gt $TargetCount) {
    # Используем Get-Random для выбора случайного набора
    $selectedIndices = $matchIndices | Get-Random -Count $TargetCount
} else {
    $selectedIndices = $matchIndices
}
# Добавляем контекст вокруг выбранных строк
$resultLines = [System.Collections.Generic.List[string]]::new()
$addedLineNumbers = [System.Collections.Generic.HashSet[int]]::new()
foreach ($idx in $selectedIndices) {
    # Определяем диапазон контекста
    $start = [Math]::Max(0, $idx - $Context)
    $end = [Math]::Min($totalLines - 1, $idx + $Context)
    
    # Добавляем строки из диапазона, которых ещё нет
    for ($j = $start; $j -le $end; $j++) {
        if (-not $addedLineNumbers.Contains($j)) {
            $addedLineNumbers.Add($j) | Out-Null
            $resultLines.Add($allLines[$j])
        }
    }
}
# Выбор случайных индексов из всех совпадений
if ($foundCount -gt $TargetCount) {
    $selectedIndices = $matchIndices | Get-Random -Count $TargetCount
    $selectedIndices = $selectedIndices | Sort-Object
} else {
    $selectedIndices = $matchIndices | Sort-Object
}
# Вывод
Write-Host "Всего совпадений в файле: $foundCount" -ForegroundColor Green
Write-Host "Показано: $($selectedIndices.Count) (строки с контекстом +/-$Context)" -ForegroundColor Gray
Write-Host "---------------------------" -ForegroundColor Gray
$prevLast = -999
foreach ($idx in $selectedIndices) {
    $start = [Math]::Max(0, $idx - $Context)
    $end   = [Math]::Min($totalLines - 1, $idx + $Context)
    if ($prevLast -ne -999 -and $start -gt ($prevLast + 1)) {
        Write-Host "---" -ForegroundColor DarkGray
    }
    for ($j = $start; $j -le $end; $j++) {
        $realNum = $j + 1
        $prefix = if ($j -eq $idx) { ">" } else { " " }
        $color = if ($j -eq $idx) { "Yellow" } else { "Gray" }
        Write-Host ("{0} {1,5}: {2}" -f $prefix, $realNum, $allLines[$j]) -ForegroundColor $color
    }
    $prevLast = $end
}
