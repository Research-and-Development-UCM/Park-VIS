param([int]$Port = 8001)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.dev-env/python.exe')) { throw 'Run setup.ps1 first.' }
$env:PATH = "$PSScriptRoot/.dev-env/Library/bin;$PSScriptRoot/.dev-env/Scripts;$env:PATH"
$env:GI_TYPELIB_PATH = "$PSScriptRoot/.dev-env/Library/lib/girepository-1.0"
$env:GST_PLUGIN_PATH = "$PSScriptRoot/.dev-env/Library/lib/gstreamer-1.0"
New-Item -ItemType Directory -Path '.local-data' -Force | Out-Null
$env:GST_REGISTRY = "$PSScriptRoot/.local-data/gst-registry.bin"
$env:PARK_VIS_HOME = "$PSScriptRoot/.local-data"
& "$PSScriptRoot/.dev-env/python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port $Port --reload --reload-dir backend
