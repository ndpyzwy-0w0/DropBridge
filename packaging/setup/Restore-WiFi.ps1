# Restores the supported RTL8821CU / RTL8811CU adapter from WinUSB back to
# its original Realtek Wi-Fi driver. Must be run elevated (Restore-WiFi.cmd
# self-elevates).
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$logPath = Join-Path $PSScriptRoot 'restore.log'
Set-Content -LiteralPath $logPath -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' start restore') -Encoding utf8

function Write-Log([string]$line) {
    Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
    Write-Host $line
}

function Find-TargetDevice {
    $present = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue)
    $dev = $present | Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C820&MI_02\*' } | Select-Object -First 1
    if (-not $dev) {
        $dev = $present | Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C811\*' } | Select-Object -First 1
    }
    return $dev
}

$enumOut = & pnputil.exe /enum-drivers 2>&1 | Out-String
$currentOem = $null
$removed = @()
foreach ($raw in ($enumOut -split "`r?`n")) {
    $line = $raw.Trim()
    $idx = $line.IndexOf(':')
    if ($idx -lt 0) { continue }
    $val = $line.Substring($idx + 1).Trim()
    if ($val -match '^oem\d+\.inf$') {
        $currentOem = $val
    } elseif ($val -ieq 'airbridge-winusb.inf' -and $currentOem) {
        if ($removed -notcontains $currentOem) { $removed += $currentOem }
        $currentOem = $null
    }
}

foreach ($oem in $removed) {
    Write-Log "Removing DropBridge driver package $oem ..."
    $out = & pnputil.exe /delete-driver $oem /uninstall /force 2>&1 | Out-String
    Write-Log $out.Trim()
}

$dev = Find-TargetDevice
if ($dev) {
    $svc = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
    Write-Log ("Target device: {0} (service={1})" -f $dev.InstanceId, $svc)
    if ($svc -eq 'WinUSB') {
        Write-Log 'Removing WinUSB device node and rescanning...'
        $rmOut = & pnputil.exe /remove-device $dev.InstanceId 2>&1 | Out-String
        Write-Log $rmOut.Trim()
        $scanOut = & pnputil.exe /scan-devices 2>&1 | Out-String
        Write-Log $scanOut.Trim()
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Milliseconds 500
            $dev = Find-TargetDevice
            if ($dev) {
                $svc = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
                if ($svc -and $svc -ne 'WinUSB') { break }
            }
        }
    }
} elseif ($removed.Count -gt 0) {
    & pnputil.exe /scan-devices 2>&1 | Out-Null
}

$final = Find-TargetDevice
if (-not $final) {
    Write-Log 'Note: adapter is not currently plugged in (DropBridge driver package cleaned up).'
    exit 0
}
$finalSvc = (Get-PnpDeviceProperty -InstanceId $final.InstanceId -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
Write-Log ("Final service: {0}" -f $finalSvc)
if ($finalSvc -eq 'WinUSB') {
    Write-Log 'FAILED: device is still bound to WinUSB. Unplug/replug or uninstall in Device Manager.'
    exit 1
}
Write-Log 'OK: restored to non-WinUSB driver.'
exit 0
