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
$modelAppDirectories = New-Object System.Collections.Generic.List[string]

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
        if ($rel -match '(^|/)models(\.py$|/)') {
            $modelsChanged = $true
            # The app is the directory that holds the models module or package.
            $appDirectory = $rel -replace '/?models(\.py$|/.*$)', ''
            if ($appDirectory -and (Test-Path -LiteralPath $rel) -and -not $modelAppDirectories.Contains($appDirectory)) { $modelAppDirectories.Add($appDirectory) }
        }
    }

    Pop-Location
} catch { Pop-Location -ErrorAction SilentlyContinue }

# An app without a migrations package: 'makemigrations' without an app label skips it, and so does the check below.
foreach ($appDirectory in $modelAppDirectories) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectDir "$appDirectory/migrations/__init__.py"))) {
        $appLabel = Split-Path $appDirectory -Leaf
        $problems.Add("App '$appDirectory' has models but no migrations package, so 'makemigrations' without an app label skips it. Run 'docker compose run --rm web python manage.py makemigrations $appLabel --name <descriptive_name>' (the app label defaults to '$appLabel') and review the result.")
    }
}

# Missing migrations: only checked when a model module changed, and only once the Compose project exists.
# Django runs only in the Compose web service, so the check runs there. '--no-deps' uses db when it is running but
# never starts it, so the check cannot initialize the database volume from an unfinished .env. Without db, Django
# still detects missing migrations and only warns that it could not check the applied-migration history.
$hasProject = (Test-Path -LiteralPath (Join-Path $projectDir 'manage.py')) -and (Test-Path -LiteralPath (Join-Path $projectDir 'compose.yaml'))
$docker = Get-Command docker -ErrorAction SilentlyContinue
$checkNote = $null
if ($modelsChanged -and $hasProject -and $docker) {
    try {
        # Windows PowerShell 5.1 turns native stderr into terminating errors under 'Stop'.
        $ErrorActionPreference = 'Continue'
        Push-Location -LiteralPath $projectDir
        # Compose writes container status lines to stderr. Converting each error record to its message keeps
        # Windows PowerShell 5.1 from formatting the first one as a full error record, and an empty one as
        # the text 'System.Management.Automation.RemoteException'.
        $output = & docker compose run --rm --no-deps web python manage.py makemigrations --check --dry-run 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } } | Out-String
        $exitCode = $LASTEXITCODE
        Pop-Location
        if ($exitCode -ne 0) {
            if ($output -match 'Migrations for') {
                $problems.Add("Model changes have no migration. Run 'docker compose run --rm web python manage.py makemigrations <app_label> --name <descriptive_name>' and review the result:`n" + $output.TrimEnd())
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
