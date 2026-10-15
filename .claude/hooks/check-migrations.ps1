# Stop hook. Enforces the CLAUDE.md migration rules at the end of every turn:
#   1. A migration that is already committed must never be edited, renamed, or deleted.
#   2. Model changes must not be left without a migration.
# Exit 0 = clean. Exit 2 = blocks the turn from ending so the problem gets fixed.
#
# To avoid a loop, a second consecutive block (stop_hook_active) is downgraded to a message
# for the user: the problem may be pre-existing work Claude cannot resolve on its own.

$ErrorActionPreference = 'Stop'

$stopHookActive = $false
try {
    $raw = [Console]::In.ReadToEnd()
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $payload = $raw | ConvertFrom-Json
        if ($payload.stop_hook_active -eq $true) { $stopHookActive = $true }
    }
} catch { }

$projectDir = $env:CLAUDE_PROJECT_DIR
if ([string]::IsNullOrWhiteSpace($projectDir)) { $projectDir = (Get-Location).Path }
if (-not (Test-Path -LiteralPath (Join-Path $projectDir '.git'))) { exit 0 }

$problems = New-Object System.Collections.Generic.List[string]
$modelsChanged = $false

try {
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $projectDir

    # Tracked files that differ from HEAD: modified, deleted, or renamed. Fails harmlessly before the first commit.
    $tracked = & git diff --name-status HEAD 2>$null
    if ($LASTEXITCODE -eq 0) {
        foreach ($entry in $tracked) {
            if ([string]::IsNullOrWhiteSpace($entry)) { continue }
            $parts = $entry -split "`t"
            $status = $parts[0]
            $file = $parts[1]
            if ($file -match '(^|/)migrations/[^/]+\.py$' -and $file -notmatch '__init__\.py$' -and $status -match '^(M|D|R)') {
                $problems.Add("Committed migration changed ($status): $file. Committed migrations are never edited, renamed, or deleted; make the file match the committed version again (read it with 'git show HEAD:$file'), then write a new migration instead.")
            }
        }
    }

    # Any model module touched in the working tree, tracked or new.
    $porcelain = & git status --porcelain --untracked-files=all 2>$null
    foreach ($entry in $porcelain) {
        if ([string]::IsNullOrWhiteSpace($entry) -or $entry.Length -lt 4) { continue }
        $rel = $entry.Substring(3).Trim().Trim('"')
        if ($rel -match '\s->\s(.+)$') { $rel = $Matches[1] }
        if ($rel -match '(^|/)models(\.py$|/)') { $modelsChanged = $true }
    }

    Pop-Location
} catch { Pop-Location -ErrorAction SilentlyContinue }

# Missing migrations: only checked when a model module changed, and only once the project environment exists.
$hasProject = (Test-Path -LiteralPath (Join-Path $projectDir 'manage.py')) -and (Test-Path -LiteralPath (Join-Path $projectDir '.venv'))
$uv = Get-Command uv -ErrorAction SilentlyContinue
$checkNote = $null
if ($modelsChanged -and $hasProject -and $uv) {
    try {
        # Windows PowerShell 5.1 turns native stderr into terminating errors under 'Stop'.
        $ErrorActionPreference = 'Continue'
        Push-Location -LiteralPath $projectDir
        $output = & uv run --quiet python manage.py makemigrations --check --dry-run 2>&1 | Out-String
        $exitCode = $LASTEXITCODE
        Pop-Location
        if ($exitCode -ne 0) {
            if ($output -match 'Migrations for') {
                $problems.Add("Model changes have no migration. Run 'uv run python manage.py makemigrations --name <descriptive_name>' and review the result:`n" + $output.TrimEnd())
            } else {
                $checkNote = "The missing-migration check could not run: " + (($output -split "`n" | Select-Object -Last 5) -join ' ').Trim()
            }
        }
    } catch { Pop-Location -ErrorAction SilentlyContinue }
}

if ($problems.Count -eq 0) {
    if ($checkNote) {
        $out = @{ systemMessage = $checkNote } | ConvertTo-Json -Compress
        [Console]::Out.Write($out)
    }
    exit 0
}

if ($stopHookActive) {
    $msg = "Migration rule problems remain after a retry, not blocking:`n" + (($problems | ForEach-Object { "  - " + $_ }) -join "`n")
    $out = @{ systemMessage = $msg } | ConvertTo-Json -Compress
    [Console]::Out.Write($out)
    exit 0
}

[Console]::Error.WriteLine("Migration rule violations (CLAUDE.md, Boundaries the user owns):")
foreach ($p in $problems) { [Console]::Error.WriteLine("  - $p") }
exit 2
