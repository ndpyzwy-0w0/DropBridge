# Read-only check of how the target adapter's Wi-Fi interface is bound
# (RTL8821CU MI_02 or RTL8811CU). Writes nothing.
# Exit codes: 0 = bound to WinUSB, 1 = still vendor/other driver, 2 = missing.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Get-DeviceProperty {
    param([string]$InstanceId, [string]$Key)
    return (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName $Key -ErrorAction SilentlyContinue).Data
}

$present = @(Get-PnpDevice -PresentOnly)
$target = $present |
    Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C820&MI_02\*' } |
    Select-Object -First 1
$model = 'RTL8821CU'
$ifaceLabel = 'MI_02'

if (-not $target) {
    $target = $present |
        Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C811\*' } |
        Select-Object -First 1
    $model = 'RTL8811CU'
    $ifaceLabel = 'Wi-Fi (non-composite)'
}

if (-not $target) {
    Write-Output 'MISSING: no supported adapter (0bda:c820 MI_02 or 0bda:c811) is present.'
    Write-Output 'Plug the adapter into a direct USB port and re-run; check tools/Inspect-Hardware.ps1.'
    exit 2
}

$inf = Get-DeviceProperty -InstanceId $target.InstanceId -Key 'DEVPKEY_Device_DriverInfPath'
$service = Get-DeviceProperty -InstanceId $target.InstanceId -Key 'DEVPKEY_Device_Service'
$version = Get-DeviceProperty -InstanceId $target.InstanceId -Key 'DEVPKEY_Device_DriverVersion'
$provider = Get-DeviceProperty -InstanceId $target.InstanceId -Key 'DEVPKEY_Device_DriverProvider'
$problem = Get-DeviceProperty -InstanceId $target.InstanceId -Key 'DEVPKEY_Device_ProblemCode'

$isWinUsb = ($service -match '^(WinUSB|libusbK|libusb0)$') -or ($inf -match 'winusb')

Write-Output ''
Write-Output ("Target Wi-Fi interface ({0}, {1})" -f $model, $ifaceLabel)
Write-Output ("  instance   : {0}" -f $target.InstanceId)
Write-Output ("  status     : {0}  (problem code {1})" -f $target.Status, $problem)
Write-Output ("  driver inf : {0}" -f $inf)
Write-Output ("  driver     : {0} {1}" -f $provider, $version)
Write-Output ("  service    : {0}" -f $service)
Write-Output ''
if ($model -eq 'RTL8821CU') {
    $bt = $present |
        Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C820&MI_00\*' } |
        Select-Object -First 1
    Write-Output 'Bluetooth interface (MI_00, must stay untouched)'
    if ($bt) {
        Write-Output ("  status     : {0}" -f $bt.Status)
    } else {
        Write-Output '  not present (adapter may be unplugged or BT disabled)'
    }
    Write-Output ''
} else {
    Write-Output 'RTL8811CU has no onboard Bluetooth; beacon uses host Bluetooth.'
    Write-Output ''
}

$net = @(Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceDescription -match '8821CU|RTL8821|8811CU|RTL8811' })
if ($net.Count -gt 0) {
    Write-Output 'Realtek network adapter(s):'
    $net | Select-Object Name, InterfaceDescription, Status | Format-Table -AutoSize
} else {
    Write-Output ("No Realtek {0} network adapter (expected once WinUSB owns it)." -f $model)
}
Write-Output ''

if ($isWinUsb) {
    Write-Output ("RESULT: {0} {1} is bound to WinUSB. libusb should be able to claim it." -f $model, $ifaceLabel)
    Write-Output 'Next: run the read-only USB open probe. Do NOT run Init yet.'
    exit 0
}

Write-Output ("RESULT: {0} {1} is still bound to a non-WinUSB driver." -f $model, $ifaceLabel)
Write-Output 'Follow docs/BINDING_RUNBOOK.md to bind it, then re-run this check.'
exit 1
