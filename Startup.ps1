$ErrorActionPreference = "Stop"

# PowerShell script to run the specified Python script,
# independent of the PWD this script is run from.

# script to run
$pyPath = ".\main.py"

# move working directory to the project folder
Write-Host ">>> cd to the project directory..."
$scriptDir = $PSScriptRoot
Set-Location $scriptDir
Write-Host "<<< Working directory set to: $PWD"
Write-Host ""

# load .env if any
if (Test-Path ".\.env") {
    Write-Host ">>> Loading .env file..."
    Get-Content ".\.env" | ForEach-Object {
        $line = $_.Trim()

        if (-not $line) { return }
        if ($line.StartsWith("#")) { return }

        $parts = $line -split "=", 2
        if ($parts.Count -eq 2) {
            $name = $parts[0].Trim()
            $value = $parts[1].Trim()
            [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    Write-Host "<<< .env file loaded"
}

# activate venv
Write-Host ">>> venv activating..."
$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Cannot find venv python: $venvPython"
}
Write-Host "<<< venv ready: $venvPython"
Write-Host ""
Write-Host ""

# run the main script
Write-Host ">>> Starting app: $pyPath ..."
Write-Host ""

& $venvPython $pyPath
$exitCode = $LASTEXITCODE

Write-Host ""
Write-Host "<<< End of the script: $pyPath"

exit $exitCode
