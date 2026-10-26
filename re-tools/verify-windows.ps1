# SPDX-FileCopyrightText: 2026 Steban Martínez Gutiérrez (Sunshide.com)
# SPDX-License-Identifier: GPL-2.0-or-later
# Read-only check of the reverse-engineered protocol on Windows (run as Administrator).
# Calls only Get* WMI methods, the same ones the Linux driver uses, and decodes them
# the same way, so the output can be compared with what NitroSense shows.
#Requires -RunAsAdministrator

$gaming  = Get-CimInstance -Namespace root\wmi -ClassName AcerGamingFunction
$generic = Get-CimInstance -Namespace root\wmi -ClassName APGeAction

function Call($obj, $method, [uint32]$arg) {
    $r = Invoke-CimMethod -InputObject $obj -MethodName $method -Arguments @{ $(if ($obj -eq $generic) { 'uiInput' } else { 'gmInput' }) = $arg }
    [uint64]$(if ($obj -eq $generic) { $r.uiOutput } else { $r.gmOutput })
}
function Byte([uint64]$v, [int]$n) { ($v -shr (8 * $n)) -band 0xFF }

$sensors = [ordered]@{ 'Temp CPU (C)' = 0x01; 'Ventilador CPU (RPM)' = 0x02; 'Temp sistema (C)' = 0x03
                       'Ventilador GPU (RPM)' = 0x06; 'Temp GPU (C)' = 0x0A }
foreach ($name in $sensors.Keys) {
    $v = Call $gaming 'GetGamingSysInfo' (0x01 -bor ($sensors[$name] -shl 8))
    if (Byte $v 0) { "{0,-22} error 0x{1:X}" -f $name, $v } else { "{0,-22} {1}" -f $name, (($v -shr 8) -band 0xFFFF) }
}

$modes = @{ 0 = 'quiet'; 1 = 'default'; 4 = 'extreme'; 5 = 'turbo' }
$sup = Byte (Call $gaming 'GetGamingMiscSetting' 0x0A) 1
$cur = Byte (Call $gaming 'GetGamingMiscSetting' 0x0B) 1
"{0,-22} {1}" -f 'Modo actual', $modes[[int]$cur]
"{0,-22} 0x{1:X2} ({2})" -f 'Modos soportados', $sup, (($modes.Keys | Sort-Object | Where-Object { $sup -band (1 -shl $_) } | ForEach-Object { $modes[$_] }) -join ' ')
foreach ($fan in @(@('CPU', 1), @('GPU', 4))) {
    "{0,-22} {1} %" -f "Velocidad pedida $($fan[0])", (Byte (Call $gaming 'GetGamingFanSpeed' $fan[1]) 1)
}
"{0,-22} {1}" -f 'CoolBoost', ((Byte (Call $generic 'GetFunction' 0x0207) 1) -eq 1)
$kb = Call $generic 'GetFunction' (0x1 -bor (132 -shl 8) -bor 0x80000)
"{0,-22} brillo {1} %, apagado 30 s: {2}" -f 'Retroiluminación', (Byte $kb 4), ((Byte $kb 5) -eq 30)
"{0,-22} {1}" -f 'LCD overdrive', ((Byte (Call $gaming 'GetGamingProfile' 0) 6) -eq 1)
foreach ($z in 0..3) {
    $c = Call $gaming 'GetGamingRgbKb' (1 -shl $z)
    "{0,-22} {1:x2}{2:x2}{3:x2}" -f "Zona $($z + 1)", (Byte $c 1), (Byte $c 2), (Byte $c 3)
}
