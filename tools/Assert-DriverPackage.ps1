# Fail the build when a staged WinUSB INF no longer matches its signed catalog.
#
#   powershell -ExecutionPolicy Bypass -File tools\Assert-DriverPackage.ps1 -DriverDir dist\DropBridge\setup\driver
#
# The catalog (.cat) embeds the SHA-1 of the INF's exact bytes; any EOL
# conversion (a stale core.autocrlf working tree from before the .gitattributes
# fix, or any staging step that normalises line endings) silently breaks the
# signature, and pnputil then refuses the package on the user's machine with
# "The hash of the file is not present in the specified catalog file".
# v0.1.0-internal.1 shipped exactly that: a CRLF INF next to a catalog signed
# over the LF bytes, so every first-run driver install failed.
#
# makecat stores each member hash as a raw 20-byte SHA-1 inside the CTL, so
# searching the catalog bytes for the INF's SHA-1 proves the match without
# needing signtool, the Windows SDK or any certificate store. Works on
# Windows PowerShell 5.1 and PowerShell 7 alike.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DriverDir
)

$ErrorActionPreference = 'Stop'
$inf = Join-Path $DriverDir 'airbridge-winusb.inf'
$cat = Join-Path $DriverDir 'airbridge-winusb.cat'
foreach ($p in @($inf, $cat)) {
    if (-not (Test-Path $p)) { throw "missing $p" }
}

$sha1 = [System.Security.Cryptography.SHA1]::Create()
try {
    $hash = $sha1.ComputeHash([System.IO.File]::ReadAllBytes($inf))
} finally {
    $sha1.Dispose()
}
$catBytes = [System.IO.File]::ReadAllBytes($cat)

$found = $false
for ($i = 0; $i -le $catBytes.Length - $hash.Length; $i++) {
    $match = $true
    for ($j = 0; $j -lt $hash.Length; $j++) {
        if ($catBytes[$i + $j] -ne $hash[$j]) { $match = $false; break }
    }
    if ($match) { $found = $true; break }
}

$hex = -join ($hash | ForEach-Object { $_.ToString('x2') })
if (-not $found) {
    throw (("staged INF does not match its signed catalog: SHA-1 of {0} is {1}, " +
            'which the catalog does not contain. The catalog was signed over ' +
            'different bytes - usually a CRLF/LF conversion of the INF. Re-stage ' +
            'packaging\driver unmodified (git checkout -- packaging/driver), or ' +
            're-sign with tools\Build-DriverPackage.ps1 if the INF really changed.') -f $inf, $hex)
}
Write-Host ("driver package OK: {0} SHA-1 {1} is signed by the catalog" -f $inf, $hex)
