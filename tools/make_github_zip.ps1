# Bundle only the files that belong on GitHub (same rules as .gitignore)
# into dist\teamproject_github_<date>.zip and dist\teamproject_github\ (folder for drag & drop upload).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$excludeDirs = '^(\.venv|recordings|runs|uploads|dist|\.git|\.claude)(\\|$)|\\__pycache__(\\|$)|^__pycache__(\\|$)'
$files = Get-ChildItem -Recurse -File -Force | Where-Object {
    $r = $_.FullName.Substring($root.Length + 1)
    -not ($r -match $excludeDirs) -and
    $_.Name -ne '.env' -and $_.Extension -ne '.pyc' -and -not $_.Name.StartsWith('~$') -and
    $r -ne 'favorites.json' -and
    $r -ne 'mock_system\mock.db' -and $r -ne 'eval\recordings_index.json' -and
    -not ($r -match '^macros\\.*\.json$') -and -not ($r -match '^manuals\\.*\.md$')
}

$dist = Join-Path $root 'dist'
$stage = Join-Path $dist 'teamproject_github'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null

foreach ($f in $files) {
    $rel = $f.FullName.Substring($root.Length + 1)
    $dest = Join-Path $stage $rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath $f.FullName -Destination $dest
}

# Safety check: secrets / personal data must never be in the bundle
$bad = Get-ChildItem -Recurse -File -Force $stage | Where-Object { $_.Name -eq '.env' -or $_.Name -eq 'mock.db' }
if ($bad) { throw "Blocked: secret or DB file found in bundle: $($bad.FullName)" }

$stamp = Get-Date -Format 'yyyyMMdd_HHmm'
$zip = Join-Path $dist "teamproject_github_$stamp.zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip)

$size = [math]::Round(((Get-ChildItem -Recurse -File -Force $stage | Measure-Object Length -Sum).Sum) / 1KB)
Write-Host ""
Write-Host "FILES=$($files.Count)  SIZE=${size}KB"
Write-Host "FOLDER=$stage"
Write-Host "ZIP=$zip"
