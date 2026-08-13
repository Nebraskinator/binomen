# collect_boot_log.ps1
#
# Finds and collects boot.log, written by the server at startup from v0.2.3 on.
#
# It exists because stderr is not a reliable channel under Claude Desktop's
# built-in-Node path: that path forks the server as an Electron UtilityProcess,
# whose default stdio mode is `inherit`, which sends the child's stderr to the
# app's own stream rather than to the per-extension log. So the server's own
# [binomen] lines simply do not appear there, healthy or not.
#
# Where the file lands is itself a finding. The server writes to
# LOCALAPPDATA\binomen, but a process inside the MSIX container may see a
# redirected LOCALAPPDATA. If a copy shows up under the Claude package's
# LocalCache and not in the plain path, the container is virtualising it -- and
# the extension has been looking for its index in the wrong place all along.
#
#   powershell -ExecutionPolicy Bypass -File scripts\collect_boot_log.ps1

$ErrorActionPreference = "Continue"
$repo   = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $repo "diag"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$report = Join-Path $outDir "boot-log-report.txt"
Remove-Item $report -ErrorAction SilentlyContinue

function Say($s) { Write-Host $s; Add-Content -Path $report -Value $s -Encoding UTF8 }

Say "binomen boot.log collection"
Say "run at $(Get-Date -Format o)"
Say ""

$candidates = @(
  "$env:LOCALAPPDATA\binomen\boot.log"
)
# Anywhere the MSIX container might have redirected it to.
$pkgRoot = "$env:LOCALAPPDATA\Packages"
if (Test-Path $pkgRoot) {
  Get-ChildItem $pkgRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "Claude" } |
    ForEach-Object {
      $candidates += (Join-Path $_.FullName "LocalCache\Local\binomen\boot.log")
      $candidates += (Join-Path $_.FullName "LocalCache\Roaming\binomen\boot.log")
    }
}

$found = 0
foreach ($c in ($candidates | Sort-Object -Unique)) {
  if (Test-Path -LiteralPath $c) {
    $found++
    Say "================================================================"
    Say "FOUND: $c"
    Say "  modified $((Get-Item -LiteralPath $c).LastWriteTime)"
    Say "================================================================"
    Get-Content -LiteralPath $c | ForEach-Object { Say "  $_" }
    Say ""
  } else {
    Say "absent: $c"
  }
}

if ($found -eq 0) {
  Say ""
  Say "No boot.log anywhere. Either v0.2.3 is not the installed build, or the"
  Say "server did not reach its first write. Sweeping more broadly:"
  foreach ($root in @($env:LOCALAPPDATA, $env:APPDATA)) {
    Get-ChildItem $root -Filter "boot.log" -Recurse -Force -ErrorAction SilentlyContinue |
      Select-Object -First 10 | ForEach-Object { Say "  candidate: $($_.FullName)" }
  }
}

# What is actually installed.
#
# Added because "no boot.log" has two readings -- the server never ran, or the
# build that writes one was never installed -- and a diagnostic that cannot
# separate them is the same mistake twice.
Say ""
Say "================================================================"
Say "Installed extensions: version actually on disk"
Say "================================================================"
$extRoot = "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\Claude Extensions"
if (Test-Path $extRoot) {
  Get-ChildItem $extRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $mani = Join-Path $_.FullName "manifest.json"
    if (Test-Path $mani) {
      try {
        $m = Get-Content $mani -Raw | ConvertFrom-Json
        Say "  $($m.name)  v$($m.version)   [$($_.Name)]"
        $entry = Join-Path $_.FullName "server\index.js"
        if (Test-Path $entry) {
          $sv = Select-String -Path $entry -Pattern 'serverInfo:\s*\{\s*name:\s*"[^"]*",\s*version:\s*"([^"]+)"' -ErrorAction SilentlyContinue
          if ($sv) { Say "      serverInfo version in code: $($sv.Matches[0].Groups[1].Value)" }
          Say "      entry modified: $((Get-Item $entry).LastWriteTime)"
          Say "      files in server\: $((Get-ChildItem (Split-Path $entry) -File | Measure-Object).Count)"
        } else {
          Say "      NO server\index.js"
        }
      } catch { Say "  $($_.Name): unreadable manifest" }
    }
  }
} else { Say "  extension root not found: $extRoot" }

# The per-extension log, for the client's side of the same startup.
Say ""
Say "================================================================"
Say "Claude Desktop's view of the same startup"
Say "================================================================"
$logDir = "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\logs"
if (Test-Path $logDir) {
  Get-ChildItem $logDir -Filter "*binomen*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 2 |
    ForEach-Object {
      Say ""
      Say "-- $($_.Name)"
      Get-Content $_.FullName -Tail 40 | ForEach-Object { Say "   $_" }
    }
}
Say ""
Say "report written to $report"
Write-Host ""
Write-Host "Report: $report" -ForegroundColor Green
