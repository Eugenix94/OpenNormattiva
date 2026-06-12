param(
    [string]$RepoRoot = "c:\Users\Dell\Documents\VSC Projects\OpenNormattiva-italianlab",
    [int]$SleepMinutes = 5,
    [int]$TargetRows = 400000,
    [string]$HfToken = ""
)

$ErrorActionPreference = "Continue"

if ($HfToken -and $HfToken.Trim().Length -gt 0) {
    $env:HF_TOKEN = $HfToken.Trim()
}

$venvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
$logDir = Join-Path $RepoRoot "logs"
$runLog = Join-Path $logDir "keep_multivigente_alive.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-RunLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $runLog -Value $line
}

function Get-RowCount {
    $code = @"
import sqlite3
from pathlib import Path
p = Path('data/multivigente.db')
if not p.exists():
    print(0)
else:
    conn = sqlite3.connect(str(p))
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM law_versions')
    print(cur.fetchone()[0])
    conn.close()
"@
    $out = $code | py - 2>$null
    if ($LASTEXITCODE -ne 0) { return 0 }
    $val = 0
    [void][int]::TryParse(($out | Select-Object -Last 1).ToString().Trim(), [ref]$val)
    return $val
}

if (-not (Test-Path $venvActivate)) {
    Write-RunLog "ERROR: Virtual environment activation script not found at $venvActivate"
    exit 1
}

Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned | Out-Null
. $venvActivate
Set-Location $RepoRoot

Write-RunLog "Auto-runner started. RepoRoot=$RepoRoot SleepMinutes=$SleepMinutes TargetRows=$TargetRows"

$lastRows = Get-RowCount
Write-RunLog "Initial row count: $lastRows"

while ($true) {
    Write-RunLog "Starting multivigente build cycle..."
    py build_voom.py --steps multivigente --skip-download 2>&1 | Tee-Object -FilePath $runLog -Append
    $buildExit = $LASTEXITCODE

    $rows = Get-RowCount
    $delta = $rows - $lastRows
    Write-RunLog "Build cycle finished. ExitCode=$buildExit Rows=$rows Delta=$delta"

    if ((Test-Path "data/multivigente.db") -and $env:HF_TOKEN -and ($env:HF_TOKEN.Trim().Length -gt 0)) {
        Write-RunLog "Uploading multivigente.db to Hugging Face dataset..."
        py upload_multivigente.py 2>&1 | Tee-Object -FilePath $runLog -Append
        $uploadExit = $LASTEXITCODE
        Write-RunLog "Upload finished. ExitCode=$uploadExit"
    }
    else {
        Write-RunLog "Skipping upload (missing DB or HF_TOKEN not set)."
    }

    if ($rows -ge $TargetRows) {
        Write-RunLog "Target reached: $rows >= $TargetRows. Continuing to keep data refreshed."
    }

    $lastRows = $rows
    Write-RunLog "Sleeping for $SleepMinutes minutes before next cycle..."
    Start-Sleep -Seconds ($SleepMinutes * 60)
}
