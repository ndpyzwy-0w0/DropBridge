# Builds the Rust GUI frontend (src/ui_rust -> build/ui_rust/DropBridgeUI.exe).
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$manifest = Join-Path $root 'src\ui_rust\Cargo.toml'
$targetDir = Join-Path $root 'build\ui_rust\target'
$outDir = Join-Path $root 'build\ui_rust'

& cargo build --release --manifest-path $manifest --target-dir $targetDir
if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }

New-Item -ItemType Directory -Force $outDir | Out-Null
$exe = Join-Path $targetDir 'release\DropBridgeUI.exe'
Copy-Item $exe (Join-Path $outDir 'DropBridgeUI.exe') -Force
Write-Host ("ui_rust: {0} ({1:N0} KB)" -f (Join-Path $outDir 'DropBridgeUI.exe'), ((Get-Item $exe).Length / 1KB))
