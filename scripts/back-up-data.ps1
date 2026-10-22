# Backs up one copy of Axisence to a folder, normally on the backup drive: a custom-format
# pg_dump of its database, and every report, teaser, and raw download not copied yet.
# Run by the user, never by Claude: daily by a Windows scheduled task in the live copy,
# and once with -Copy Development in the development copy to move its data.
#
# Destination layout:
#   <Destination>\database\axisence-<yyyyMMdd-HHmmss>.dump   the newest -DumpsToKeep dumps
#   <Destination>\reports\     every report and teaser file, never pruned
#   <Destination>\raw\         every raw download, never pruned
#   <Destination>\backup-log.txt
#
# Invocation, in the copy's own folder:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\back-up-data.ps1 -Destination E:\Axisence\live
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\back-up-data.ps1 -Copy Development -Destination E:\Axisence\move
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [ValidateSet('Live', 'Development')][string]$Copy = 'Live',
    [ValidateRange(1, 1000)][int]$DumpsToKeep = 30
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repositoryRoot = Split-Path -Parent $PSScriptRoot

# The image installs four extensions into every new database, with the schemas tiger,
# tiger_data, and topology. The dump leaves all of them out, because a restore into a
# fresh live database stops at the first object that already exists there. The single
# quotes keep $POSTGRES_USER and $POSTGRES_DB for the container's shell, which expands
# them from the service's environment.
$dumpCommand = 'pg_dump --format=custom' +
    ' --exclude-extension=postgis --exclude-extension=postgis_topology' +
    ' --exclude-extension=fuzzystrmatch --exclude-extension=postgis_tiger_geocoder' +
    ' --exclude-schema=tiger --exclude-schema=tiger_data --exclude-schema=topology' +
    ' --username=$POSTGRES_USER --dbname=$POSTGRES_DB'

$exitCode = 0
Push-Location -LiteralPath $repositoryRoot
try {
    if ($Copy -eq 'Live') {
        $composeFile = 'compose.live.yaml'
        if (-not (Test-LiveCopyFolder $repositoryRoot)) {
            throw 'This folder is not the live copy: its .env does not set COMPOSE_FILE=compose.live.yaml.'
        }
    } else {
        $composeFile = 'compose.yaml'
        if (Test-LiveCopyFolder $repositoryRoot) {
            throw 'This folder is the live copy. Back up the development copy from its own folder.'
        }
    }

    # The destination is never created here, so a disconnected drive never sends a backup
    # onto another drive.
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        throw "The backup destination $Destination is not reachable. Connect the backup drive, or create the folder."
    }
    $destinationRoot = (Resolve-Path -LiteralPath $Destination).ProviderPath
    $databaseFolder = Join-Path $destinationRoot 'database'
    foreach ($folder in @($databaseFolder, (Join-Path $destinationRoot 'reports'), (Join-Path $destinationRoot 'raw'))) {
        if (-not (Test-Path -LiteralPath $folder -PathType Container)) {
            New-Item -ItemType Directory -Path $folder | Out-Null
        }
    }

    $dumpFileName = 'axisence-{0}.dump' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    $dumpPath = Join-Path $databaseFolder $dumpFileName
    # Named after the run, so a scheduled backup and a release's backup running at once
    # never write or remove each other's dump in the container.
    $containerDumpPath = "/tmp/$dumpFileName"
    try {
        Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'exec', '-T', 'db', 'sh', '-c', "$dumpCommand --file=$containerDumpPath")
        Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'cp', "db:$containerDumpPath", $dumpPath)
    } finally {
        # No copy of personal data stays in the container, whatever happened.
        & docker compose -f $composeFile exec -T db rm -f $containerDumpPath
    }

    $backupFolderOfDataFolder = [ordered]@{ 'data\private\reports' = 'reports'; 'data\raw' = 'raw' }
    foreach ($dataFolder in $backupFolderOfDataFolder.Keys) {
        $dataFolderPath = Join-Path $repositoryRoot $dataFolder
        if (Test-Path -LiteralPath $dataFolderPath -PathType Container) {
            Copy-NewFiles $dataFolderPath (Join-Path $destinationRoot $backupFolderOfDataFolder[$dataFolder])
        }
    }

    # File names hold the time of the backup, so ordering by name orders by time.
    Get-ChildItem -LiteralPath $databaseFolder -Filter 'axisence-*.dump' -File |
        Sort-Object Name -Descending |
        Select-Object -Skip $DumpsToKeep |
        Remove-Item

    $dumpSize = (Get-Item -LiteralPath $dumpPath).Length
    $logLine = '{0} {1} backup succeeded: database\{2} ({3} bytes); new reports and raw downloads copied.' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Copy, $dumpFileName, $dumpSize
    Add-Content -LiteralPath (Join-Path $destinationRoot 'backup-log.txt') -Value $logLine -Encoding UTF8
    Write-Output $logLine
} catch {
    $message = $_.Exception.Message
    if (Test-Path -LiteralPath $Destination -PathType Container) {
        $logLine = '{0} {1} backup failed: {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Copy, $message
        Add-Content -LiteralPath (Join-Path $Destination 'backup-log.txt') -Value $logLine -Encoding UTF8
    }
    [Console]::Error.WriteLine($message)
    $exitCode = 1
} finally {
    Pop-Location
}
exit $exitCode
