# Helpers of the live copy's scripts, dot-sourced by back-up-data.ps1, restore-data.ps1,
# and release-live-copy.ps1. ASCII only: Windows PowerShell 5.1 reads a file without a
# byte order mark in the ANSI code page.

# Runs a native command and throws when it exits with a non-zero code; its arguments never hold secrets, so the message names the first one.
function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath $($ArgumentList[0]) exited with code $LASTEXITCODE."
    }
}

# Copies the files missing from or different in the destination, leaves out .part files still being written, and never deletes any.
function Copy-NewFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    & robocopy $Source $Destination /E /XF *.part /R:2 /W:5 /NP /NFL /NDL /NJH /NJS
    # robocopy's exit codes 0 to 7 mean success.
    if ($LASTEXITCODE -ge 8) {
        throw "Copying $Source to $Destination failed with robocopy code $LASTEXITCODE."
    }
}

# Returns whether the folder is the live copy, whose .env sets COMPOSE_FILE=compose.live.yaml; reads nothing else from .env.
function Test-LiveCopyFolder {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $environmentFile = Join-Path $RepositoryRoot '.env'
    if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
        return $false
    }
    # Windows PowerShell 5.1's -Quiet returns nothing rather than $false without a match.
    return [bool](Select-String -LiteralPath $environmentFile -Pattern '^\s*COMPOSE_FILE\s*=\s*compose\.live\.yaml\s*$' -Quiet)
}
