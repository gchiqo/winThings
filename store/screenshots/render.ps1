<#
  Renders every scene (*.html) in this folder to png\<name>.png at 3840x2160
  (1920x1080 at 2x), ready to upload as Store screenshots.

  The scenes use real captures from the app in assets\: the settings window
  (dark and light) and the paused reading panel. Retake those when the UI
  changes.

  Run:  .\store\screenshots\render.ps1
#>
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge not found." }

$out = Join-Path $here "png"
New-Item -ItemType Directory -Force $out | Out-Null
# Own profile, so a running Edge window isn't touched.
$profileDir = Join-Path $env:TEMP "read-selected-render"

Get-ChildItem $here -Filter "*.html" | Sort-Object Name | ForEach-Object {
    $png = Join-Path $out ($_.BaseName + ".png")
    & $edge --headless=new --disable-gpu --hide-scrollbars --no-first-run `
        "--user-data-dir=$profileDir" --force-device-scale-factor=2 `
        --window-size=1920,1080 --virtual-time-budget=8000 `
        "--screenshot=$png" ([uri]$_.FullName).AbsoluteUri 2>$null | Out-Null
    Write-Host "  $($_.BaseName).png"
}
Write-Host "Done: $out"
