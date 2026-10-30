<#
.SYNOPSIS
  Build the Read Selected MSIX package ready for Microsoft Store submission.

.DESCRIPTION
  Steps:
    1. Runs PyInstaller to produce dist\reader\ (the app bundle).
    2. Generates Store icon assets via store\make_assets.py.
    3. Assembles the MSIX staging directory:
         msix_staging\
           reader.exe + all PyInstaller output
           Assets\         (all PNG icons)
           AppxManifest.xml
    4. Calls makeappx.exe to create the .msix file.
    5. (Optional) Signs with a self-signed cert for local testing.

  For Store submission you do NOT sign locally — Partner Center signs the
  package automatically during ingestion. The signing step here is only for
  testing the installation on your own machine before uploading.

.NOTES
  Prerequisites (install once):
    - Windows SDK  (for makeappx.exe and signtool.exe)
      winget install --id Microsoft.WindowsSDK.10.0.22621
    - PyInstaller
      uv pip install pyinstaller
    - All reader dependencies already in the .venv

  Run from the repo root:
    .\store\build_msix.ps1
  Or with signing for local testing:
    .\store\build_msix.ps1 -SignForTesting
#>

param(
    [switch]$SignForTesting,
    [string]$Version = "1.0.0.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$RepoDir   = Split-Path -Parent $PSScriptRoot      # winThings\
$StoreDir  = $PSScriptRoot                          # winThings\store\
$Staging   = Join-Path $RepoDir "msix_staging"
$DistDir   = Join-Path $RepoDir "dist\reader"
$OutMsix   = Join-Path $RepoDir "dist\ReadSelected_$Version.msix"

# Find makeappx.exe from the installed Windows SDK
function Find-WinSdkTool([string]$Name) {
    $kitsRoot = "C:\Program Files (x86)\Windows Kits\10\bin"
    if (-not (Test-Path $kitsRoot)) {
        throw "Windows SDK not found at $kitsRoot. Install it with: winget install --id Microsoft.WindowsSDK.10.0.22621"
    }
    $tool = Get-ChildItem $kitsRoot -Filter $Name -Recurse |
        Where-Object { $_.FullName -match "x64" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $tool) {
        throw "$Name not found in $kitsRoot. Make sure the Windows SDK is installed."
    }
    return $tool.FullName
}

$MakeAppx = Find-WinSdkTool "makeappx.exe"
Write-Host "makeappx.exe: $MakeAppx" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Step 1: PyInstaller
# ---------------------------------------------------------------------------
Write-Host "`n[1/4] Running PyInstaller..." -ForegroundColor Yellow
$PythonExe = Join-Path $RepoDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) { $PythonExe = "python" }

& $PythonExe -m PyInstaller reader\reader.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

if (-not (Test-Path $DistDir)) {
    throw "PyInstaller output not found at $DistDir"
}
Write-Host "  Bundle: $DistDir" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 2: Generate Store assets
# ---------------------------------------------------------------------------
Write-Host "`n[2/4] Generating Store assets..." -ForegroundColor Yellow
& $PythonExe (Join-Path $StoreDir "make_assets.py")
if ($LASTEXITCODE -ne 0) { throw "make_assets.py failed." }

# ---------------------------------------------------------------------------
# Step 3: Assemble staging directory
# ---------------------------------------------------------------------------
Write-Host "`n[3/4] Assembling MSIX staging directory..." -ForegroundColor Yellow
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item -ItemType Directory -Path $Staging | Out-Null

# Copy the PyInstaller bundle
Copy-Item "$DistDir\*" $Staging -Recurse

# Copy Assets
Copy-Item (Join-Path $StoreDir "Assets") $Staging -Recurse

# Copy and patch manifest (update version)
$ManifestSrc = Join-Path $StoreDir "AppxManifest.xml"
$ManifestDst = Join-Path $Staging "AppxManifest.xml"
$content = Get-Content $ManifestSrc -Raw
# Case-sensitive and whitespace-anchored: only <Identity Version>, not the
# <?xml version> declaration or TargetDeviceFamily MinVersion.
$content = $content -creplace '(?<=\sVersion=")[^"]*', $Version
Set-Content $ManifestDst $content -Encoding UTF8

Write-Host "  Staging: $Staging" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 4: Create the .msix file
# ---------------------------------------------------------------------------
Write-Host "`n[4/4] Creating $OutMsix ..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path (Split-Path $OutMsix) | Out-Null

& $MakeAppx pack /d $Staging /p $OutMsix /overwrite
if ($LASTEXITCODE -ne 0) { throw "makeappx failed." }
Write-Host "  Package: $OutMsix" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Optional: sign with a self-signed cert for local testing
# ---------------------------------------------------------------------------
if ($SignForTesting) {
    Write-Host "`n[+] Signing with a local test certificate..." -ForegroundColor Yellow
    $SignTool  = Find-WinSdkTool "signtool.exe"
    $CertPfx   = Join-Path $StoreDir "TestCert.pfx"
    $CertPass  = "test"
    $CertPwd   = ConvertTo-SecureString -String $CertPass -Force -AsPlainText
    # signtool refuses unless the cert subject equals the manifest Publisher.
    $Publisher = ([xml]$content).Package.Identity.Publisher

    if (Test-Path $CertPfx) {
        $existing = (Get-PfxData -FilePath $CertPfx -Password $CertPwd).EndEntityCertificates[0]
        if ($existing.Subject -ne $Publisher) { Remove-Item $CertPfx }
    }
    if (-not (Test-Path $CertPfx)) {
        Write-Host "  Generating self-signed test certificate for $Publisher ..." -ForegroundColor Cyan
        $cert = New-SelfSignedCertificate `
            -Type Custom -Subject $Publisher `
            -KeyUsage DigitalSignature `
            -FriendlyName "ReadSelected Test" `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
        Export-PfxCertificate -Cert $cert -FilePath $CertPfx -Password $CertPwd | Out-Null
        Write-Host "  Certificate saved to $CertPfx" -ForegroundColor Green
        Write-Host "  IMPORTANT: Install this cert as a Trusted Root CA (or Trusted People)"
        Write-Host "  before sideloading.  Run once in an admin PowerShell:"
        Write-Host "    Import-PfxCertificate -FilePath '$CertPfx' -CertStoreLocation Cert:\LocalMachine\TrustedPeople -Password (ConvertTo-SecureString '$CertPass' -Force -AsPlainText)"
    }

    & $SignTool sign /fd SHA256 /a /f $CertPfx /p $CertPass $OutMsix
    if ($LASTEXITCODE -ne 0) { throw "signtool failed." }
    Write-Host "  Signed: $OutMsix" -ForegroundColor Green
}

Write-Host "`nDone.  Package: $OutMsix" -ForegroundColor Green
Write-Host @"

Next steps:
  Testing locally:
    .\store\build_msix.ps1 -SignForTesting
    Then double-click $OutMsix to install (or Add-AppxPackage).

  Store submission:
    Upload $OutMsix to Partner Center — do NOT sign it first.
    Partner Center signs the package during certification.
    See STORE_GUIDE.md for full submission instructions.
"@
