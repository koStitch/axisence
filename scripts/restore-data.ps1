# Restores a backup made by back-up-data.ps1 into a fresh live copy only: the newest dump
# of <Source>\database, or -DumpFileName, into the live database in one transaction, then
# the files of <Source>\reports and <Source>\raw. It refuses a live copy that already
# holds data. Run by the user, never by Claude, in the live copy's folder:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restore-data.ps1 -Source E:\Axisence\move
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$DumpFileName
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$composeFile = 'compose.live.yaml'

$exitCode = 0
Push-Location -LiteralPath $repositoryRoot
try {
    if (-not (Test-LiveCopyFolder $repositoryRoot)) {
        throw 'This folder is not the live copy: its .env does not set COMPOSE_FILE=compose.live.yaml.'
    }

    $databaseFolder = Join-Path $Source 'database'
    if ($DumpFileName) {
        $dumpPath = Join-Path $databaseFolder $DumpFileName
        if (-not (Test-Path -LiteralPath $dumpPath -PathType Leaf)) {
            throw "No backup dump at $dumpPath."
        }
        $dumpFile = Get-Item -LiteralPath $dumpPath
    } else {
        # File names hold the time of the backup, so the last name is the newest dump.
        $dumpFile = Get-ChildItem -LiteralPath $databaseFolder -Filter 'axisence-*.dump' -File -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            Select-Object -First 1
        if (-not $dumpFile) {
            throw "No backup dump in $databaseFolder."
        }
    }

    foreach ($dataFolder in @('data\private\reports', 'data\raw')) {
        $dataFolderPath = Join-Path $repositoryRoot $dataFolder
        if ((Test-Path -LiteralPath $dataFolderPath -PathType Container) -and
            (Get-ChildItem -LiteralPath $dataFolderPath -File -Recurse | Select-Object -First 1)) {
            throw "$dataFolder already holds files. Restore only into a fresh live copy."
        }
    }

    Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'up', '-d', '--wait', 'db')

    # The query goes through standard input, because Windows PowerShell 5.1 mangles quotes
    # in the arguments of native commands.
    $migrationTableExists = "select to_regclass('public.django_migrations') is not null;" |
        & docker compose -f $composeFile exec -T db sh -c 'psql --username=$POSTGRES_USER --dbname=$POSTGRES_DB --tuples-only --no-align --quiet'
    if ($LASTEXITCODE -ne 0) {
        throw "Checking the live database failed with code $LASTEXITCODE."
    }
    if (($migrationTableExists -join '').Trim() -ne 'f') {
        throw 'The live database already holds data. Restore only into a fresh live copy.'
    }

    Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'cp', $dumpFile.FullName, 'db:/tmp/axisence-restore.dump')
    try {
        # One transaction, so a failure writes nothing.
        Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'exec', '-T', 'db', 'sh', '-c', 'pg_restore --no-owner --no-privileges --single-transaction --exit-on-error --username=$POSTGRES_USER --dbname=$POSTGRES_DB /tmp/axisence-restore.dump')
    } finally {
        # No copy of personal data stays in the container, whatever happened.
        & docker compose -f $composeFile exec -T db rm -f /tmp/axisence-restore.dump
    }

    $dataFolderOfBackupFolder = [ordered]@{ 'reports' = 'data\private\reports'; 'raw' = 'data\raw' }
    foreach ($backupFolder in $dataFolderOfBackupFolder.Keys) {
        $backupFolderPath = Join-Path $Source $backupFolder
        if (Test-Path -LiteralPath $backupFolderPath -PathType Container) {
            Copy-NewFiles $backupFolderPath (Join-Path $repositoryRoot $dataFolderOfBackupFolder[$backupFolder])
        }
    }

    Write-Output "Restored $($dumpFile.Name). Next: docker compose run --rm web python manage.py migrate, then docker compose up -d."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $exitCode = 1
} finally {
    Pop-Location
}
exit $exitCode
