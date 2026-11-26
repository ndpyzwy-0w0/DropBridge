# Sends the Bluetooth advertisement an Apple device sends before an AirDrop,
# so that devices nearby turn AWDL on and announce themselves.
# Supports:
# - DROPBRIDGE_BLE_HASHES: comma-separated 4-hex-char (2-byte) SHA-256 prefixes
#   for Contacts-Only wakeups (e.g. "a1b2,c3d4"). Falls back to random non-zero bytes.
# - DROPBRIDGE_BEACON_PULSE: path to a trigger file; when created/updated, stops
#   advertising for 2.5s and restarts with fresh payload to create a BLE wake edge.
# Windows PowerShell 5.1 only: PowerShell 7 cannot load the Bluetooth API.
$ErrorActionPreference = 'Stop'
$AppleCompanyId = 0x004C
$StartWaitSeconds = 5

function Say($line) {
  [Console]::Out.WriteLine($line)
  [Console]::Out.Flush()
}

function Build-AirDropPayload {
  $hashes = New-Object byte[] 8
  $rng = New-Object Random
  $rng.NextBytes($hashes)
  # Ensure non-zero random fallback so iOS does not ignore all-zero static ads
  if (($hashes | Where-Object { $_ -ne 0 }).Count -eq 0) { $hashes[0] = 0x5A }

  $raw = $env:DROPBRIDGE_BLE_HASHES
  if (-not $raw -and $env:DROPBRIDGE_IDENTITY_DIR) {
    $hashFile = Join-Path $env:DROPBRIDGE_IDENTITY_DIR 'ble_hashes.txt'
    if (Test-Path $hashFile) {
      $raw = (Get-Content -Path $hashFile -Raw -ErrorAction SilentlyContinue)
    }
  }
  if ($raw) {
    $tokens = $raw -split '[,;\s]+' | Where-Object { $_ -match '^[0-9a-fA-F]{4}$' } | Select-Object -First 4
    $idx = 0
    foreach ($tok in $tokens) {
      $hashes[$idx * 2] = [Convert]::ToByte($tok.Substring(0, 2), 16)
      $hashes[$idx * 2 + 1] = [Convert]::ToByte($tok.Substring(2, 2), 16)
      $idx++
    }
    # Zero out unused hash slots when real contact hashes are provided
    for ($j = $idx * 2; $j -lt 8; $j++) {
      $hashes[$j] = 0
    }
  }
  [byte[]]$payload = @(0x05, 0x12) + (,0 * 8) + @(0x01) + $hashes + @(0x00)
  return ,$payload
}

try {
  $namespace = 'Windows.Devices.Bluetooth.Advertisement'
  [void][Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementPublisher, Windows.Devices.Bluetooth.Advertisement, ContentType = WindowsRuntime]
  [void][Windows.Devices.Bluetooth.Advertisement.BluetoothLEManufacturerData, Windows.Devices.Bluetooth.Advertisement, ContentType = WindowsRuntime]
  Add-Type -AssemblyName System.Runtime.WindowsRuntime

  $metadata = "$env:windir\System32\WinMetadata"
  $compiler = New-Object System.CodeDom.Compiler.CompilerParameters
  $compiler.GenerateInMemory = $true
  $compiler.ReferencedAssemblies.AddRange([string[]]@(
    [Reflection.Assembly]::Load('System.Runtime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a').Location,
    "$metadata\Windows.Devices.winmd", "$metadata\Windows.Foundation.winmd"))
  Add-Type -CompilerParameters $compiler -TypeDefinition @'
using Windows.Devices.Bluetooth.Advertisement;
public static class AirDropBeacon {
  public static void Add(BluetoothLEAdvertisementPublisher publisher, BluetoothLEManufacturerData data) {
    publisher.Advertisement.ManufacturerData.Add(data);
  }
}
'@

  function Start-BeaconPublisher([byte[]]$payload) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
      $data = New-Object "$namespace.BluetoothLEManufacturerData"
      $data.CompanyId = $AppleCompanyId
      $data.Data = [Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::AsBuffer($payload)
      $pub = New-Object "$namespace.BluetoothLEAdvertisementPublisher"
      [AirDropBeacon]::Add($pub, $data)
      $pub.Start()
      $deadline = (Get-Date).AddSeconds($StartWaitSeconds)
      while ("$($pub.Status)" -notin 'Started', 'Aborted' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 100
      }
      if ("$($pub.Status)" -eq 'Started') {
        return $pub
      }
      Say "Bluetooth attempt $attempt status $($pub.Status)"
      try { $pub.Stop() } catch {}
      Start-Sleep -Milliseconds 500
    }
    return $null
  }

  $payload = Build-AirDropPayload
  if ($args -contains '-DryRun') {
    $hex = ($payload | ForEach-Object { $_.ToString('x2') }) -join ''
    Say "would advertise $($payload.Length) bytes ($hex)"
    return
  }

  $publisher = Start-BeaconPublisher $payload
  if (-not $publisher) {
    Say 'Bluetooth did not start advertising after 3 attempts'
    return
  }
  Say 'advertising'

  $pulseFile = $env:DROPBRIDGE_BEACON_PULSE
  while ($true) {
    Start-Sleep -Milliseconds 500
    if ($pulseFile -and (Test-Path $pulseFile)) {
      Remove-Item -Path $pulseFile -Force -ErrorAction SilentlyContinue
      try { $publisher.Stop() } catch {}
      Start-Sleep -Milliseconds 2500
      $payload = Build-AirDropPayload
      $publisher = Start-BeaconPublisher $payload
      if ($publisher) {
        Say 're-pulsed'
      } else {
        Say 're-pulse failed'
        return
      }
    } elseif ("$($publisher.Status)" -eq 'Aborted') {
      Say 'publisher aborted, restarting'
      try { $publisher.Stop() } catch {}
      Start-Sleep -Milliseconds 1000
      $payload = Build-AirDropPayload
      $publisher = Start-BeaconPublisher $payload
      if (-not $publisher) { return }
      Say 'advertising'
    }
  }
} catch {
  Say "Bluetooth advertising failed: $($_.Exception.Message)"
}
