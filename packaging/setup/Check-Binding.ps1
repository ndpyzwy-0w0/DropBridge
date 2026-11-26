# One-time setup check for DropBridge: is the Wi-Fi interface of a supported
# adapter (RTL8821CU MI_02 or RTL8811CU) bound to WinUSB? Read-only.
#
#   exit 0  already bound (nothing to do)
#   exit 1  present, but still on the Realtek driver (binding needed)
#   exit 3  no supported card is plugged in
param([switch]$Quiet)

$ErrorActionPreference = 'Continue'
$present = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue)
$device = $present |
    Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C820&MI_02\*' } |
    Select-Object -First 1
$label = 'RTL8821CU MI_02'

if (-not $device) {
    $device = $present |
        Where-Object { $_.InstanceId -like 'USB\VID_0BDA&PID_C811\*' } |
        Select-Object -First 1
    $label = 'RTL8811CU'
}

if (-not $device) {
    if (-not $Quiet) {
        Write-Host 'NOT PRESENT: no supported card (8821CU/8811CU) is plugged in.' -ForegroundColor Yellow
        Write-Host 'Plug it into a rear USB port and run this again.'
    }
    exit 3
}

$service = (Get-PnpDeviceProperty -InstanceId $device.InstanceId `
        -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data

if ($service -eq 'WinUSB') {
    if (-not $Quiet) {
        Write-Host ("OK: {0} is bound to WinUSB." -f $label) -ForegroundColor Green
        Write-Host ('    ' + $device.InstanceId)
    }
    exit 0
}

if (-not $Quiet) {
    Write-Host ("NOT YET: {0} is using {1}, so libusb cannot open it." -f $label, $service) -ForegroundColor Yellow
    Write-Host 'Run setup\Bind-WinUSB.cmd to bind it to WinUSB (one time per machine).'
}
exit 1
