<#
.SYNOPSIS
    Runs generate_acopos_parameter_list.py inside the virtual environment.

.DESCRIPTION
    Creates the virtual environment (.venv) including the dependencies from
    requirements.txt if it does not exist yet, and then starts the Python
    script. Any additional arguments are forwarded to the Python script.

.EXAMPLE
    .\run_acopos_parameter_list.ps1

#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$venvPath = Join-Path $root '.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
$requirements = Join-Path $root 'requirements.txt'
$script = Join-Path $root 'generate_acopos_parameter_list.py'

function Get-BasePython {
    foreach ($candidate in @('py', 'python3', 'python')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            if ($candidate -eq 'py') { return @{ Exe = $cmd.Source; Args = @('-3') } }
            return @{ Exe = $cmd.Source; Args = @() }
        }
    }
    throw 'Python was not found. Please install Python 3 and make it available in PATH.'
}

if (-not (Test-Path -LiteralPath $script)) {
    throw "Python script not found: $script"
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating virtual environment: $venvPath" -ForegroundColor Cyan
    $base = Get-BasePython
    & $base.Exe @($base.Args + @('-m', 'venv', $venvPath))
    if ($LASTEXITCODE -ne 0) { throw "Creating the virtual environment failed (exit code $LASTEXITCODE)." }

    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Updating pip failed (exit code $LASTEXITCODE)." }

    if (Test-Path -LiteralPath $requirements) {
        Write-Host 'Installing dependencies...' -ForegroundColor Cyan
        & $venvPython -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw "Installing the dependencies failed (exit code $LASTEXITCODE)." }
    }
}

& $venvPython $script @ScriptArgs
exit $LASTEXITCODE
