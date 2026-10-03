param([int]$Port = 5175, [int]$BackendPort = 8002)
$ErrorActionPreference = 'Stop'
$env:PATH = "$PSScriptRoot/.dev-env;$PSScriptRoot/.dev-env/Scripts;$env:PATH"
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $node) { throw 'Run setup.ps1 first or install Node.js.' }
$env:VITE_API_TARGET = "http://127.0.0.1:$BackendPort"
Set-Location "$PSScriptRoot/frontend"
& $node.Source node_modules/vite/bin/vite.js --host 127.0.0.1 --port $Port --strictPort
