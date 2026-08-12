# Thin wrapper. The real installer is install_claude_desktop.py.
#
# An earlier version of this file implemented the logic in PowerShell and
# shipped with a null-reference bug, because it could not be run on the machine
# where it was written. The Python version is covered by tests/test_installer.py.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_claude_desktop.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_claude_desktop.ps1 -Descriptions imperative
#   powershell -ExecutionPolicy Bypass -File scripts\install_claude_desktop.ps1 -Remove
param(
    [ValidateSet("narrow", "broad", "imperative")]
    [string]$Descriptions = "broad",
    [switch]$Remove,
    [switch]$Force
)
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$argv = @((Join-Path $PSScriptRoot "install_claude_desktop.py"), "--descriptions", $Descriptions)
if ($Remove) { $argv += "--remove" }
if ($Force)  { $argv += "--force" }
& $py @argv
exit $LASTEXITCODE
