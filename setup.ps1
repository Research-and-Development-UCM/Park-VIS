param([switch]$FrontendOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:CONDA_PKGS_DIRS = "$PSScriptRoot/.tools/pkgs"

if (-not $FrontendOnly) {
    $manager = Join-Path $PSScriptRoot '.tools/Library/bin/micromamba.exe'
    if (-not (Test-Path -LiteralPath $manager)) {
        New-Item -ItemType Directory -Path '.tools' -Force | Out-Null
        Invoke-WebRequest 'https://micro.mamba.pm/api/micromamba/win-64/latest' -OutFile '.tools/micromamba.tar.bz2'
        & tar -xf '.tools/micromamba.tar.bz2' -C '.tools'
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the dependency manager.' }
    }
    & $manager --root-prefix "$PSScriptRoot/.tools/mamba-cache" create --yes --channel conda-forge --prefix "$PSScriptRoot/.dev-env" python=3.12 pygobject gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav ffmpeg nodejs=22
    if ($LASTEXITCODE -ne 0) { throw 'Dependency environment installation failed.' }
    $env:PATH = "$PSScriptRoot/.dev-env;$PSScriptRoot/.dev-env/Library/bin;$PSScriptRoot/.dev-env/Scripts;$env:PATH"
    & "$PSScriptRoot/.dev-env/python.exe" -m pip install -r requirements.txt pefile
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    & "$PSScriptRoot/.dev-env/python.exe" -m pip install 'http://download.lotvulture.com/dist/vulturevision/vulturevision-1.0.0-cp312-cp312-win_amd64.whl'
    if ($LASTEXITCODE -ne 0) { throw 'AI engine installation failed.' }
    Copy-Item -LiteralPath 'scripts/sitecustomize.py' -Destination '.dev-env/Lib/site-packages/sitecustomize.py' -Force
    & "$PSScriptRoot/.dev-env/python.exe" windows/patch_pyd.py .dev-env/Lib/site-packages/vulturevision/_vulturevision.pyd
    if ($LASTEXITCODE -ne 0) { throw 'AI engine Windows compatibility patch failed.' }
}
$env:PATH = "$PSScriptRoot/.dev-env;$PSScriptRoot/.dev-env/Scripts;$env:PATH"
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { throw 'Install Node.js 22+ with npm, or run setup.ps1 without -FrontendOnly.' }
Push-Location frontend
try {
    & $npm.Source install --package-lock-only --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'Dependency lock update failed.' }
    & $npm.Source ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & $npm.Source run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Park-VIS is ready. Run start-backend.ps1 and start-frontend.ps1 in separate terminals.'
