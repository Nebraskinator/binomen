# set_instructions_variant.ps1
#
# Switch which `instructions` text the INSTALLED extension sends, without
# rebuilding or reinstalling.
#
#   powershell -ExecutionPolicy Bypass -File scripts\set_instructions_variant.ps1 unconditional
#   powershell -ExecutionPolicy Bypass -File scripts\set_instructions_variant.ps1 conditional
#   powershell -ExecutionPolicy Bypass -File scripts\set_instructions_variant.ps1 off
#   powershell -ExecutionPolicy Bypass -File scripts\set_instructions_variant.ps1 status
#
# It edits `server.mcp_config.env` in the installed manifest.json. Claude
# Desktop passes that block to the server process as environment variables, so
# this is the supported way to hand a setting to an extension without shipping
# a different build.
#
# Why this exists: the instruction text is a treatment, not a feature, and
# comparing two treatments means changing one thing between runs. Rebuilding
# the .mcpb to flip it would change the build as well, and reinstalling resets
# this file -- so the comparison has to be doable in place.
#
# NOTE: reinstalling the extension overwrites the manifest and reverts this to
# the shipped default. Re-run after any reinstall, and run `status` before a
# measurement rather than trusting memory.

param(
  [Parameter(Position = 0)]
  [ValidateSet("conditional", "unconditional", "off", "status")]
  [string]$Variant = "status"
)

$ErrorActionPreference = "Stop"

$extRoot = "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\Claude Extensions"
if (-not (Test-Path $extRoot)) {
  Write-Host "Extension directory not found: $extRoot" -ForegroundColor Red
  Write-Host "Is Claude Desktop installed from the Store (MSIX)?"
  exit 1
}

$mani = Get-ChildItem $extRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $m = Join-Path $_.FullName "manifest.json"
  if (Test-Path $m) {
    try {
      $j = Get-Content $m -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($j.name -eq "binomen") { $m }
    } catch { }
  }
} | Select-Object -First 1

if (-not $mani) {
  Write-Host "binomen is not installed. Install dist\binomen.mcpb first." -ForegroundColor Red
  exit 1
}

$json = Get-Content $mani -Raw -Encoding UTF8 | ConvertFrom-Json

# Read the current setting before touching anything.
$current = "conditional (default - no env override set)"
if ($json.server.mcp_config.PSObject.Properties.Name -contains "env" -and $json.server.mcp_config.env) {
  $envObj = $json.server.mcp_config.env
  if ($envObj.PSObject.Properties.Name -contains "BINOMEN_INSTRUCTIONS") {
    $current = "$($envObj.BINOMEN_INSTRUCTIONS)  (explicitly set)"
  }
}

Write-Host ""
Write-Host "manifest : $mani"
Write-Host "version  : $($json.version)"
Write-Host "current  : $current"

if ($Variant -eq "status") {
  Write-Host ""
  Write-Host "Pass conditional, unconditional or off to change it."
  exit 0
}

# Rebuild the env object rather than mutating in place: PSCustomObject from
# ConvertFrom-Json does not reliably accept new members added by assignment.
$newEnv = @{}
if ($json.server.mcp_config.PSObject.Properties.Name -contains "env" -and $json.server.mcp_config.env) {
  foreach ($p in $json.server.mcp_config.env.PSObject.Properties) { $newEnv[$p.Name] = $p.Value }
}
$newEnv["BINOMEN_INSTRUCTIONS"] = $Variant

$json.server.mcp_config | Add-Member -NotePropertyName env -NotePropertyValue $newEnv -Force

# Write WITHOUT a byte-order mark.
#
# `Set-Content -Encoding UTF8` in Windows PowerShell 5.1 emits a UTF-8 BOM, and
# JSON.parse throws on a leading BOM. An earlier version of this script did
# exactly that and made the installed manifest unreadable to the host. The
# symptom was an extension that silently did not start -- which is
# indistinguishable, from the chat window, from an instruction variant that had
# no effect. It invalidated a full hand comparison before anyone noticed.
#
# [IO.File]::WriteAllText writes UTF-8 with no BOM.
$text = $json | ConvertTo-Json -Depth 40
[System.IO.File]::WriteAllText($mani, $text, (New-Object System.Text.UTF8Encoding($false)))

# Verify from raw bytes, not through PowerShell's reader -- which strips a BOM
# on the way in and would cheerfully report success on a file the host cannot
# read. Checking the artifact the way its actual consumer sees it is the whole
# lesson of this project.
$bytes = [System.IO.File]::ReadAllBytes($mani)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
  Write-Host ""
  Write-Host "FAILED: wrote a BOM. The host will not be able to parse this." -ForegroundColor Red
  Write-Host "Reinstall dist\binomen.mcpb to restore a clean manifest." -ForegroundColor Red
  exit 1
}

$raw = [System.Text.Encoding]::UTF8.GetString($bytes)
try {
  $check = $raw | ConvertFrom-Json
} catch {
  Write-Host ""
  Write-Host "FAILED: the manifest no longer parses. Reinstall dist\binomen.mcpb." -ForegroundColor Red
  exit 1
}

# The edit must not have disturbed how the server is launched.
if (-not $check.server.mcp_config.command -or -not $check.server.mcp_config.args) {
  Write-Host ""
  Write-Host "FAILED: command/args missing after the edit. Reinstall dist\binomen.mcpb." -ForegroundColor Red
  exit 1
}

$applied = $check.server.mcp_config.env.BINOMEN_INSTRUCTIONS
Write-Host "new      : $applied"

if ($applied -ne $Variant) {
  Write-Host ""
  Write-Host "FAILED: the file does not read back with the value that was written." -ForegroundColor Red
  exit 1
}

Write-Host ""
Write-Host "Set to '$Variant'. RESTART CLAUDE DESKTOP for it to take effect." -ForegroundColor Green
Write-Host "Quit fully - closing the window is not enough - then reopen." -ForegroundColor Green
Write-Host ""
Write-Host "Verify after restarting: scripts\collect_boot_log.bat" -ForegroundColor DarkGray
