# check_manifest_bom.ps1
#
# Did set_instructions_variant.ps1 corrupt the installed manifest?
#
# Windows PowerShell 5.1's `Set-Content -Encoding UTF8` writes a UTF-8
# byte-order mark. JSON.parse throws on a leading BOM, so a manifest rewritten
# that way can become unreadable to the host -- and the symptom is an extension
# that silently does not start, which is indistinguishable from an instruction
# variant that had no effect.
#
#   powershell -ExecutionPolicy Bypass -File scripts\check_manifest_bom.ps1

$ErrorActionPreference = "Continue"

$extRoot = "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\Claude Extensions"
$dirs = Get-ChildItem $extRoot -Directory -ErrorAction SilentlyContinue

foreach ($d in $dirs) {
  $m = Join-Path $d.FullName "manifest.json"
  if (-not (Test-Path $m)) { continue }

  Write-Host ""
  Write-Host "=============================================================="
  Write-Host $m

  $bytes = [System.IO.File]::ReadAllBytes($m)
  $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
  Write-Host ("  first 3 bytes : {0:X2} {1:X2} {2:X2}" -f $bytes[0], $bytes[1], $bytes[2])
  if ($hasBom) {
    Write-Host "  BOM           : PRESENT  <-- this breaks JSON.parse" -ForegroundColor Red
  } else {
    Write-Host "  BOM           : absent" -ForegroundColor Green
  }

  # Parse it the way a JSON parser would, from raw bytes, not through
  # PowerShell's reader (which strips the BOM and would hide the problem).
  $text = [System.Text.Encoding]::UTF8.GetString($bytes)
  try {
    $null = $text | ConvertFrom-Json
    Write-Host "  parses (PS)   : yes"
  } catch {
    Write-Host "  parses (PS)   : NO - $($_.Exception.Message)" -ForegroundColor Red
  }

  if (Get-Command node -ErrorAction SilentlyContinue) {
    $probe = @"
const fs = require('fs');
const raw = fs.readFileSync(process.argv[1]);
try { JSON.parse(raw.toString('utf8')); console.log('  parses (node) : yes'); }
catch (e) { console.log('  parses (node) : NO - ' + e.message); }
"@
    $tmp = Join-Path $env:TEMP "binomen-json-probe.js"
    Set-Content -LiteralPath $tmp -Value $probe -Encoding ASCII
    & node $tmp $m
  }

  try {
    $j = [System.Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
    Write-Host "  name          : $($j.name)  v$($j.version)"
    Write-Host "  command       : $($j.server.mcp_config.command)"
    Write-Host "  args          : $($j.server.mcp_config.args -join ' ')"
    $envKeys = @()
    if ($j.server.mcp_config.env) { $envKeys = $j.server.mcp_config.env.PSObject.Properties.Name }
    Write-Host "  env           : $(if ($envKeys) { $envKeys -join ', ' } else { '(empty)' })"
    if ($envKeys -contains "BINOMEN_INSTRUCTIONS") {
      Write-Host "  variant       : $($j.server.mcp_config.env.BINOMEN_INSTRUCTIONS)"
    }
  } catch {
    Write-Host "  could not read fields" -ForegroundColor Red
  }
}

Write-Host ""
Write-Host "If BOM is PRESENT above, reinstall dist\binomen.mcpb to restore a clean"
Write-Host "manifest, then use the fixed set_instructions_variant.ps1."
