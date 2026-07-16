param(
    [string]$SourcePath = "F:\Trading Intelligence SaaS",
    [string]$ExportPath = "F:\Trading-Intelligence-SaaS-Clean"
)

$ErrorActionPreference = "Stop"

function Normalize-Path([string]$PathValue) {
    return [System.IO.Path]::GetFullPath($PathValue).TrimEnd('\', '/')
}

function Get-RelativePathCompat([string]$BasePath, [string]$ChildPath) {
    $baseFull = Normalize-Path $BasePath
    $childFull = [System.IO.Path]::GetFullPath($ChildPath)
    $baseUri = [System.Uri]::new($baseFull + [System.IO.Path]::DirectorySeparatorChar)
    $childUri = [System.Uri]::new($childFull)
    $relativeUri = $baseUri.MakeRelativeUri($childUri).ToString()
    return [System.Uri]::UnescapeDataString($relativeUri).Replace('/', [System.IO.Path]::DirectorySeparatorChar)
}

function Add-Warning([System.Collections.Generic.List[string]]$Warnings, [string]$Message) {
    [void]$Warnings.Add($Message)
}

$SourcePath = Normalize-Path $SourcePath
$ExportPath = Normalize-Path $ExportPath
$Warnings = [System.Collections.Generic.List[string]]::new()
$Excluded = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$CopiedCount = 0

if (-not (Test-Path -LiteralPath $SourcePath -PathType Container)) {
    throw "Source path does not exist: $SourcePath"
}

if ($ExportPath -eq $SourcePath -or $ExportPath.StartsWith("$SourcePath\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to export inside the source project: $ExportPath"
}

if (Test-Path -LiteralPath $ExportPath) {
    Remove-Item -LiteralPath $ExportPath -Recurse -Force
}
New-Item -ItemType Directory -Path $ExportPath | Out-Null

$ExcludedDirectoryNames = @(
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".vscode",
    ".idea",
    "logs",
    "log",
    "backups",
    "backup",
    "coverage",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
    "out",
    "runtime"
)

$ExcludedFileNames = @(
    ".env",
    "local.db",
    ".coverage"
)

$AllowedEnvExamples = @(".env.example", ".env.pi.example")

function Should-ExcludeDirectory([System.IO.DirectoryInfo]$Directory) {
    $name = $Directory.Name
    if ($ExcludedDirectoryNames -contains $name) {
        return $true
    }
    if ($name -like "tmp*") {
        return $true
    }
    if ($name -match "(?i)docker.*volume|volume|volumes") {
        return $true
    }
    return $false
}

function Should-ExcludeFile([System.IO.FileInfo]$File) {
    $name = $File.Name
    if ($AllowedEnvExamples -contains $name) {
        return $false
    }
    if ($ExcludedFileNames -contains $name) {
        return $true
    }
    if ($name -like ".env.*") {
        return $true
    }
    if ($name -like "*.pyc" -or $name -like "*.pyo") {
        return $true
    }
    if ($name -like "*.db" -or $name -like "*.sqlite" -or $name -like "*.sqlite3") {
        return $true
    }
    if ($name -like "*.log") {
        return $true
    }
    if ($name -match "(?i)(secret|token|credential|private[_-]?key).*\.(json|txt|key|pem|env)$") {
        return $true
    }
    return $false
}

function Mark-Excluded([string]$PathValue) {
    $relative = Get-RelativePathCompat $SourcePath $PathValue
    [void]$Excluded.Add($relative)
}

function Copy-Tree([string]$CurrentSource) {
    foreach ($item in Get-ChildItem -LiteralPath $CurrentSource -Force) {
        if ($item.PSIsContainer) {
            if (Should-ExcludeDirectory $item) {
                Mark-Excluded $item.FullName
                continue
            }
            $relativeDir = Get-RelativePathCompat $SourcePath $item.FullName
            $targetDir = Join-Path $ExportPath $relativeDir
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            Copy-Tree $item.FullName
            continue
        }

        if (Should-ExcludeFile $item) {
            Mark-Excluded $item.FullName
            continue
        }

        $relativeFile = Get-RelativePathCompat $SourcePath $item.FullName
        $targetFile = Join-Path $ExportPath $relativeFile
        $targetParent = Split-Path -Parent $targetFile
        if (-not (Test-Path -LiteralPath $targetParent)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }
        Copy-Item -LiteralPath $item.FullName -Destination $targetFile -Force
        $script:CopiedCount += 1
    }
}

Copy-Tree $SourcePath

foreach ($envExample in $AllowedEnvExamples) {
    $sourceEnv = Join-Path $SourcePath $envExample
    $targetEnv = Join-Path $ExportPath $envExample
    if ((Test-Path -LiteralPath $sourceEnv) -and -not (Test-Path -LiteralPath $targetEnv)) {
        Copy-Item -LiteralPath $sourceEnv -Destination $targetEnv -Force
        $CopiedCount += 1
    }
}

$nestedGit = Get-ChildItem -LiteralPath $ExportPath -Force -Recurse -Directory -Filter ".git" -ErrorAction SilentlyContinue
if ($nestedGit) {
    $paths = ($nestedGit | ForEach-Object { $_.FullName }) -join "`n"
    throw "Nested .git folder detected in clean export:`n$paths"
}

$SecretPatterns = [ordered]@{
    "OpenAI/Generic bearer token" = "Bearer\s+[A-Za-z0-9_\-\.=]{24,}"
    "GitHub token" = "\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b|github_pat_[A-Za-z0-9_]{30,}"
    "Stripe secret" = "\bsk_(live|test)_[A-Za-z0-9]{16,}\b"
    "Stripe webhook secret" = "\bwhsec_[A-Za-z0-9]{16,}\b"
    "Telegram bot token" = "\b\d{8,12}:AA[A-Za-z0-9_-]{20,}\b"
    "OANDA API key" = "\b[a-f0-9]{24,}-[a-f0-9]{16,}\b"
    "Finnhub API key assignment" = "(?i)FINNHUB_API_KEY\s*=\s*['""]?[A-Za-z0-9]{16,}"
    "Twelve Data API key assignment" = "(?i)TWELVE_DATA_API_KEY\s*=\s*['""]?[A-Za-z0-9]{16,}"
    "JWT secret assignment" = "(?i)JWT(_REFRESH)?_SECRET\s*=\s*['""]?[A-Za-z0-9_\-]{24,}"
    "Password assignment" = "(?i)\b(password|postgres_password|admin_password)\s*[:=]\s*['""][^'""]{8,}['""]"
    "Private key" = "-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
}

$PlaceholderPattern = "(?i)(replace|change[-_ ]?me|placeholder|example\.com|PI_TAILSCALE_IP|YOUR_|your-|random|strong|temporary|changeme|sample|dummy)"
$RuntimeLookupPattern = "(?i)(os\.getenv|getenv|settings\.|getattr|argparse|args\.|input\(|getpass|environ)"

$SecretHits = @()
$TextFileExtensions = @(
    ".py", ".ps1", ".sh", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml", ".toml",
    ".ini", ".md", ".txt", ".env", ".example", ".dockerignore", ".gitignore", ".sql"
)

$filesToScan = Get-ChildItem -LiteralPath $ExportPath -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $ext = $_.Extension.ToLowerInvariant()
        $TextFileExtensions -contains $ext -or $_.Name -in @(".env.example", ".env.pi.example", ".gitignore", ".dockerignore")
    }

foreach ($file in $filesToScan) {
    $lines = Get-Content -LiteralPath $file.FullName -ErrorAction SilentlyContinue
    if ($null -eq $lines) {
        continue
    }
    foreach ($patternName in $SecretPatterns.Keys) {
        $lineNumber = 0
        foreach ($line in $lines) {
            $lineNumber += 1
            if ($line -notmatch $SecretPatterns[$patternName]) {
                continue
            }
            if ($line -match $PlaceholderPattern -or $line -match $RuntimeLookupPattern -or $line.TrimStart().StartsWith("#")) {
                continue
            }
            $relative = Get-RelativePathCompat $ExportPath $file.FullName
            $SecretHits += "${relative}:$lineNumber :: $patternName"
        }
    }
}

if ($SecretHits.Count -gt 0) {
    Write-Host "SECRET SCAN FAILED" -ForegroundColor Red
    $SecretHits | Sort-Object -Unique | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    throw "Clean export contains likely real secrets."
}

Push-Location $ExportPath
try {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $pythonDirs = @("app", "runner", "scripts", "tests", "alembic") | Where-Object { Test-Path -LiteralPath $_ }
        if ($pythonDirs.Count -gt 0) {
            & python -m compileall -q @pythonDirs
            if ($LASTEXITCODE -ne 0) {
                throw "Python syntax checks failed."
            }
        }
    } else {
        Add-Warning $Warnings "python not found; skipped Python syntax checks."
    }

    if ((Test-Path -LiteralPath "trading-ui/package.json") -and (Test-Path -LiteralPath "trading-ui/node_modules")) {
        Push-Location "trading-ui"
        try {
            & npm exec -- tsc --noEmit
            if ($LASTEXITCODE -ne 0) {
                throw "Next.js typecheck failed."
            }
        } finally {
            Pop-Location
        }
    } else {
        Add-Warning $Warnings "trading-ui/node_modules not present in clean export; skipped Next.js typecheck."
    }

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker -and (Test-Path -LiteralPath "docker-compose.pi.yml")) {
        $createdTempEnv = $false
        if (-not (Test-Path -LiteralPath ".env")) {
            Copy-Item -LiteralPath ".env.pi.example" -Destination ".env" -Force
            $createdTempEnv = $true
        }
        try {
            & docker compose -f docker-compose.pi.yml --env-file .env.pi.example config --quiet
            if ($LASTEXITCODE -ne 0) {
                throw "docker compose config check failed."
            }
        } finally {
            if ($createdTempEnv -and (Test-Path -LiteralPath ".env")) {
                Remove-Item -LiteralPath ".env" -Force
            }
        }
    } else {
        Add-Warning $Warnings "docker not found or docker-compose.pi.yml missing; skipped docker compose config check."
    }
} finally {
    Pop-Location
}

Get-ChildItem -LiteralPath $ExportPath -Force -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $ExportPath -Force -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
    Remove-Item -Force

$excludedList = $Excluded | Sort-Object
Write-Host "CLEAN EXPORT READY"
Write-Host "Export path: $ExportPath"
Write-Host "Files copied: $CopiedCount"
Write-Host "Files/directories excluded: $($excludedList.Count)"
foreach ($item in $excludedList) {
    Write-Host "  excluded: $item"
}
if ($Warnings.Count -gt 0) {
    Write-Host "Validation warnings:"
    foreach ($warning in $Warnings) {
        Write-Host "  warning: $warning"
    }
} else {
    Write-Host "Validation warnings: none"
}
