# PostToolUse hook (Write|Edit). Flags Python rule violations that CLAUDE.md states but a model
# can shortcut past, then runs ruff on the file when the project environment exists.
# Advisory: PostToolUse cannot undo the write; exit 2 surfaces stderr back to Claude.
# Exit 0 = clean. Exit 2 = violations found.

$ErrorActionPreference = 'Stop'

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
} catch { exit 0 }

$path = $payload.tool_input.file_path
if ([string]::IsNullOrWhiteSpace($path)) { exit 0 }
if ($path -notmatch '\.py$') { exit 0 }
if (-not (Test-Path -LiteralPath $path)) { exit 0 }

# Throwaway analysis scripts are exempt from every rule.
if ($path -match '[\\/]scratch[\\/]') { exit 0 }

try { $lines = @(Get-Content -LiteralPath $path -ErrorAction Stop) } catch { exit 0 }
if ($lines.Count -eq 0) { exit 0 }

$leaf = Split-Path $path -Leaf
$isTest = ($path -match '[\\/]tests?[\\/]') -or ($leaf -match '^test_.*\.py$') -or ($leaf -match '_test\.py$') -or ($leaf -eq 'conftest.py')
$isMigration = $path -match '[\\/]migrations[\\/]'
$isManagementCommand = $path -match '[\\/]management[\\/]commands[\\/]'
$isSettings = ($path -match '[\\/]settings[\\/]') -or ($leaf -eq 'settings.py')

# Migrations are generated code; only the committed-migration rule (Stop hook) applies to them.
if ($isMigration) { exit 0 }

$violations = New-Object System.Collections.Generic.List[string]

for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    $n = $i + 1
    $trimmed = $line.TrimStart()
    if ($trimmed.StartsWith('#')) { continue }
    # Drop a trailing comment so text in comments cannot trigger a rule.
    $code = ($line -replace '\s+#[^''"]*$', '')

    # Naive datetimes.
    if ($code -match '\bdatetime\.(now|utcnow|today)\s*\(') {
        $violations.Add("L${n}: naive datetime. Use 'django.utils.timezone.now()' (CLAUDE.md: datetimes are timezone-aware).")
    }

    # print() outside tests.
    if (-not $isTest -and ($code -match '(^|[^\w\.])print\s*\(')) {
        if ($isManagementCommand) {
            $violations.Add("L${n}: 'print' in a management command. Write user-facing output to 'self.stdout.write' or 'self.stderr.write'.")
        } else {
            $violations.Add("L${n}: 'print' outside tests and scratch scripts. Log through the 'logging' module.")
        }
    }

    # SQL built by formatting.
    if ($code -match '\.(execute|executemany|raw|extra)\s*\(') {
        if (($code -match '\.(execute|executemany|raw|extra)\s*\(\s*[rRbB]?[fF][rR]?["'']') -or ($code -match '["'']\s*%\s*[\(\w]') -or ($code -match '\.format\s*\(')) {
            $violations.Add("L${n}: SQL built by string formatting. Use the ORM or pass parameters separately.")
        }
    }

    # Bare except, and swallowed exceptions.
    if ($code -match '^\s*except\s*:') {
        $violations.Add("L${n}: bare 'except:'. Catch the specific exception.")
    }
    if ($code -match '^\s*except\b.*:\s*$') {
        $j = $i + 1
        while ($j -lt $lines.Count -and [string]::IsNullOrWhiteSpace($lines[$j])) { $j++ }
        if ($j -lt $lines.Count -and $lines[$j].Trim() -eq 'pass') {
            $violations.Add("L${n}: exception swallowed with 'pass'. Handle it, log it, or let it propagate.")
        }
    }

    # Wildcard imports outside the settings package.
    if (-not $isSettings -and ($code -match '^\s*from\s+\S+\s+import\s+\*')) {
        $violations.Add("L${n}: wildcard import. Import the names explicitly.")
    }

    # global statements.
    if ($code -match '^\s*global\s+\w') {
        $violations.Add("L${n}: 'global' statement. Pass state explicitly or hold it in an object.")
    }

    # Hard-coded secrets. Tests may carry obvious fake values.
    if (-not $isTest -and ($code -match '(?i)\b\w*(api_?key|secret|token|password|passwd)\w*\s*[:=]\s*[rRbB]?["''](?!https?://)[^"''\s]{12,}["'']')) {
        $violations.Add("L${n}: looks like a hard-coded secret. Read it from settings, which read the environment; document the variable in '.env.example'.")
    }

    # Nullable string fields.
    if (($code -match '\b(CharField|TextField|SlugField|EmailField|URLField)\s*\(') -and ($code -match 'null\s*=\s*True') -and ($code -notmatch 'unique\s*=\s*True')) {
        $violations.Add("L${n}: 'null=True' on a string field. Use the empty string as the empty value (django-python skill).")
    }

    # Float money.
    if (($code -match '\bFloatField\s*\(') -and ($code -match '(?i)(price|fee|tuition|rate|cost|deposit|amount|charge|discount)')) {
        $violations.Add("L${n}: money stored as 'FloatField'. Use 'DecimalField' (CLAUDE.md: money is Decimal).")
    }

    # HTTP calls without a timeout. The call may span several lines, so scan to its closing parenthesis.
    if ($code -match '\b(requests|httpx)\.(get|post|put|patch|delete|head|options|request|stream)\s*\(') {
        $span = ''
        $depth = 0
        $started = $false
        for ($k = $i; $k -lt [Math]::Min($lines.Count, $i + 30); $k++) {
            $segment = $lines[$k]
            if (-not $started) {
                $segment = $segment.Substring($segment.IndexOf($Matches[0]))
                $started = $true
            }
            $span += $segment + "`n"
            $depth += ([regex]::Matches($segment, '\(')).Count - ([regex]::Matches($segment, '\)')).Count
            if ($depth -le 0) { break }
        }
        if ($span -notmatch '\btimeout\s*=') {
            $violations.Add("L${n}: HTTP call without an explicit 'timeout='.")
        }
    }
}

# Ruff, when the project environment exists. Skipped silently before the scaffold.
$projectDir = $env:CLAUDE_PROJECT_DIR
if (-not [string]::IsNullOrWhiteSpace($projectDir)) {
    $hasProject = (Test-Path -LiteralPath (Join-Path $projectDir 'pyproject.toml')) -and (Test-Path -LiteralPath (Join-Path $projectDir '.venv'))
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($hasProject -and $uv) {
        try {
            # Windows PowerShell 5.1 turns native stderr into terminating errors under 'Stop'.
            $ErrorActionPreference = 'Continue'
            Push-Location -LiteralPath $projectDir
            $ruffOutput = & uv run --quiet ruff check --quiet --no-fix $path 2>&1
            $ruffExit = $LASTEXITCODE
            Pop-Location
            if ($ruffExit -eq 1) {
                $violations.Add("ruff check reported problems:`n" + (($ruffOutput | Out-String).TrimEnd()))
            }
        } catch { Pop-Location -ErrorAction SilentlyContinue }
    }
}

if ($violations.Count -eq 0) { exit 0 }

$rel = $path
if ($projectDir) { $rel = $path.Replace($projectDir, '').TrimStart('\', '/') }

[Console]::Error.WriteLine("CLAUDE.md rule violations in ${rel}:")
foreach ($v in ($violations | Select-Object -Unique)) { [Console]::Error.WriteLine("  - $v") }
[Console]::Error.WriteLine("Fix these before continuing.")
exit 2
