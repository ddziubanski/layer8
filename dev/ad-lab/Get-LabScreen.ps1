# SPDX-License-Identifier: AGPL-3.0-or-later
# Save a screenshot of a lab VM's console to C:\layer8-lab\build\<Name>-screen.png.
param([Parameter(Mandatory)][string]$Name, [int]$Width = 1024, [int]$Height = 768)
Add-Type -AssemblyName System.Drawing
$svc = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_VirtualSystemManagementService
$cs = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem -Filter "ElementName='$Name'"
$vssd = Get-CimAssociatedInstance -InputObject $cs -ResultClassName Msvm_VirtualSystemSettingData | Where-Object VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized'
$r = Invoke-CimMethod -InputObject $svc -MethodName GetVirtualSystemThumbnailImage -Arguments @{ TargetSystem = $vssd; WidthPixels = [uint16]$Width; HeightPixels = [uint16]$Height }
$bmp = New-Object Drawing.Bitmap $Width, $Height, ([Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$data = $bmp.LockBits((New-Object Drawing.Rectangle 0, 0, $Width, $Height), 'WriteOnly', $bmp.PixelFormat)
[Runtime.InteropServices.Marshal]::Copy([byte[]]$r.ImageData, 0, $data.Scan0, $Width * $Height * 2)   # ImageData carries 4 extra bytes
$bmp.UnlockBits($data)
$out = "C:\layer8-lab\build\$Name-screen.png"
$bmp.Save($out, [Drawing.Imaging.ImageFormat]::Png)
$out
