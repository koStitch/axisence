# Releases what is on GitHub's main to the live copy. It backs the live copy up first,
# then fast-forwards the checkout, builds the image, applies migrations, and recreates
# web. Run by the user, never by Claude, in the live copy's folder, after pushing to main:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-live-copy.ps1 -BackupDestination E:\Axisence\live
param([Parameter(Mandatory = $true)][string]$BackupDestination)

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

    $localChanges = Invoke-CheckedCommand git @('status', '--porcelain')
    if ($localChanges) {
        throw "The live copy has local changes. It must match GitHub's main exactly."
    }
    $branch = Invoke-CheckedCommand git @('rev-parse', '--abbrev-ref', 'HEAD')
    if ($branch -ne 'main') {
        throw "The live copy is on $branch, not main."
    }

    & (Join-Path $PSScriptRoot 'back-up-data.ps1') -Destination $BackupDestination
    if ($LASTEXITCODE -ne 0) {
        throw 'The backup failed, so nothing was released.'
    }

    Invoke-CheckedCommand git @('pull', '--ff-only')
    # Always builds: an unchanged Dockerfile and uv.lock make it a cached no-op.
    Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'build')
    Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'run', '--rm', 'web', 'python', 'manage.py', 'migrate')
    Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'up', '-d')
    # gunicorn loads the code once, and the bind-mounted code changed without the container
    # changing.
    Invoke-CheckedCommand docker @('compose', '-f', $composeFile, 'up', '-d', '--no-deps', '--force-recreate', 'web')

    $releasedCommit = Invoke-CheckedCommand git @('log', '-1', '--format=%h %s')
    Write-Output "The live copy now runs $releasedCommit."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $exitCode = 1
} finally {
    Pop-Location
}
exit $exitCode
