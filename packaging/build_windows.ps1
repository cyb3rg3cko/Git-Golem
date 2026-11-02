# Builds the Windows .exe and an installer (Inno Setup) into dist\.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Optional environment variable:
#   VERSION   e.g. 1.2.0 or v1.2.0 (default: latest git tag, else 0.1.0)
#
# Needs Python 3.8+ (python.org installer, which includes Tkinter) and Inno Setup 6
# (winget install JRSoftware.InnoSetup). In GitHub Actions, Inno Setup is installed automatically.

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

$App = "GitProjectManager"
$Version = $env:VERSION
if (-not $Version) {
    $ErrorActionPreference = "Continue"
    $Version = git describe --tags --abbrev=0 2>$null
    $ErrorActionPreference = "Stop"
    if (-not $Version) { $Version = "0.1.0" }
}
$Version = $Version.Trim().TrimStart("v")

if (-not (Test-Path "icon.png")) { throw "icon.png is missing from the project folder." }

Write-Host "==> Building $App $Version"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Invoke-Native python @("-m", "venv", "build\venv")
$Py = Join-Path $Root "build\venv\Scripts\python.exe"
Invoke-Native $Py @("-m", "pip", "install", "--quiet", "--upgrade", "pip", "pyinstaller", "pillow")
Invoke-Native $Py @("packaging\make_icons.py")

Write-Host "==> PyInstaller"
Invoke-Native $Py @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", $App,
    "--icon", "$Root\build\icon.ico",
    "--add-data", "$Root\icon.png;.",
    "--workpath", "build\pyinstaller", "--specpath", "build", "--distpath", "dist",
    "git_project_manager.py")

Write-Host "==> Installer"
$Iscc = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $Iscc -and $env:CI) {
    Invoke-Native choco @("install", "innosetup", "-y", "--no-progress")
    $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
if (-not $Iscc) { throw "Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup" }

Invoke-Native $Iscc @("/Q", "/DAppVersion=$Version", "packaging\installer.iss")
Move-Item "dist\$App.exe" "dist\$App-$Version-windows-portable.exe" -Force

Write-Host "==> Done:"
Get-ChildItem dist | Format-Table Name, Length
