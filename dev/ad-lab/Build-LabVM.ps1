# SPDX-License-Identifier: AGPL-3.0-or-later
# Layer 8 test lab: build one VM unattended on the L8Lab switch (10.88.0.0/24).
#   powershell -ExecutionPolicy Bypass -File C:\layer8-lab\Build-LabVM.ps1 -Name DC01
# Needs Hyper-V Administrators (not admin). Build DC01 first; the others join corp.example.com.
# Lab only: the admin password is generated once into secrets\lab-admin-password.txt.
# Copy this folder to C:\layer8-lab first; ISOs go in C:\isos (see docs/ad-lab.md).
param(
    [Parameter(Mandatory)][ValidateSet('DC01', 'RELAY01', 'WS01')][string]$Name,
    [switch]$Replace
)
$ErrorActionPreference = 'Stop'
$Lab = 'C:\layer8-lab'

$vms = @{
    DC01    = @{ Iso = 'C:\isos\*SERVER_EVAL*.iso'; Image = 2; Ip = '10.88.0.10'; Mac = '00155D880010'; Join = $false; Ou = $null }
    RELAY01 = @{ Iso = 'C:\isos\*SERVER_EVAL*.iso'; Image = 2; Ip = '10.88.0.20'; Mac = '00155D880020'; Join = $true; Ou = 'OU=Relays,DC=corp,DC=example,DC=com' }
    WS01    = @{ Iso = 'C:\isos\*CLIENTENTERPRISEEVAL*.iso'; Image = 1; Ip = $null; Mac = '00155D880030'; Join = $true; Ou = 'OU=Workstations,DC=corp,DC=example,DC=com' }
}
$vm = $vms[$Name]
$iso = Get-ChildItem $vm.Iso | Select-Object -First 1
if (-not $iso) { throw "No ISO matches $($vm.Iso). Download the evaluation ISO into C:\isos." }
$vm.Iso = $iso.FullName

# One lab admin password: local Administrator, DSRM and (on DC01) the domain Administrator.
$pwFile = "$Lab\secrets\lab-admin-password.txt"
if (-not (Test-Path $pwFile)) {
    New-Item -ItemType Directory -Force "$Lab\secrets" | Out-Null
    $chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] 20
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $pw = 'L8-' + (-join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })) + '-9a'
    Set-Content -NoNewline -Path $pwFile -Value $pw
}
$pw = (Get-Content -Raw $pwFile).Trim()

$existing = Get-VM -Name $Name -ErrorAction SilentlyContinue
if ($existing) {
    if (-not $Replace) { throw "$Name already exists. Use -Replace to delete and rebuild it." }
    Stop-VM -Name $Name -TurnOff -Force -ErrorAction SilentlyContinue
    Remove-VM -Name $Name -Force
    Remove-Item -Recurse -Force "$Lab\vms\$Name" -ErrorAction SilentlyContinue
}

# --- answer file ---------------------------------------------------------------
$macDashed = ($vm.Mac -split '(..)' | Where-Object { $_ }) -join '-'
$x = 'processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'
$locale = '<InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>'
$dns = if ($Name -eq 'DC01') { '127.0.0.1' } else { '10.88.0.10' }
$net = ''
if ($vm.Ip) {
    $net = @"
<component name="Microsoft-Windows-TCPIP" $x><Interfaces><Interface wcm:action="add">
 <Ipv4Settings><DhcpEnabled>false</DhcpEnabled></Ipv4Settings><Ipv6Settings><DhcpEnabled>false</DhcpEnabled></Ipv6Settings>
 <Identifier>$macDashed</Identifier>
 <UnicastIpAddresses><IpAddress wcm:action="add" wcm:keyValue="1">$($vm.Ip)/24</IpAddress></UnicastIpAddresses>
 <Routes><Route wcm:action="add"><Identifier>0</Identifier><Prefix>0.0.0.0/0</Prefix><NextHopAddress>10.88.0.1</NextHopAddress></Route></Routes>
</Interface></Interfaces></component>
<component name="Microsoft-Windows-DNS-Client" $x><Interfaces><Interface wcm:action="add">
 <Identifier>$macDashed</Identifier>
 <DNSServerSearchOrder><IpAddress wcm:action="add" wcm:keyValue="1">$dns</IpAddress></DNSServerSearchOrder>
</Interface></Interfaces></component>
"@
}
$join = ''
if ($vm.Join) {
    $join = @"
<component name="Microsoft-Windows-UnattendedJoin" $x><Identification>
 <Credentials><Domain>CORP</Domain><Username>Administrator</Username><Password>$pw</Password></Credentials>
 <JoinDomain>corp.example.com</JoinDomain><MachineObjectOU>$($vm.Ou)</MachineObjectOU>
</Identification></component>
"@
}
# Windows 11 refuses VMs without a TPM; the lab skips those checks instead of needing a key protector.
$bypass = ''
if ($Name -eq 'WS01') {
    $cmds = 'BypassTPMCheck', 'BypassSecureBootCheck', 'BypassRAMCheck' | ForEach-Object -Begin { $i = 0 } -Process {
        $i++; "<RunSynchronousCommand wcm:action=`"add`"><Order>$i</Order><Path>reg add HKLM\SYSTEM\Setup\LabConfig /v $_ /t REG_DWORD /d 1 /f</Path></RunSynchronousCommand>"
    }
    $bypass = "<RunSynchronous>$($cmds -join '')</RunSynchronous>"
}
# Servers log on as the built-in Administrator once to run C:\lab\setup.ps1 from the config disc.
# The workstation only joins the domain and gets a local labadmin account.
if ($Name -eq 'WS01') {
    $accounts = "<UserAccounts><LocalAccounts><LocalAccount wcm:action=`"add`"><Name>labadmin</Name><Group>Administrators</Group><Password><Value>$pw</Value><PlainText>true</PlainText></Password></LocalAccount></LocalAccounts></UserAccounts>"
    $logon = ''
} else {
    $accounts = "<UserAccounts><AdministratorPassword><Value>$pw</Value><PlainText>true</PlainText></AdministratorPassword></UserAccounts>"
    $logon = @"
<AutoLogon><Enabled>true</Enabled><Domain>$Name</Domain><Username>Administrator</Username><Password><Value>$pw</Value><PlainText>true</PlainText></Password><LogonCount>1</LogonCount></AutoLogon>
<FirstLogonCommands>
 <SynchronousCommand wcm:action="add"><Order>1</Order><CommandLine>cmd /c "for %d in (D E F G H) do @if exist %d:\lab\setup.ps1 xcopy /e /i /y %d:\lab C:\lab"</CommandLine></SynchronousCommand>
 <SynchronousCommand wcm:action="add"><Order>2</Order><CommandLine>powershell -NoProfile -ExecutionPolicy Bypass -File C:\lab\setup.ps1</CommandLine></SynchronousCommand>
</FirstLogonCommands>
"@
}

$unattend = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
<settings pass="windowsPE">
<component name="Microsoft-Windows-International-Core-WinPE" $x><SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>$locale</component>
<component name="Microsoft-Windows-Setup" $x>
 $bypass
 <DiskConfiguration><Disk wcm:action="add"><DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
  <CreatePartitions>
   <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
   <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
   <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
  </CreatePartitions>
  <ModifyPartitions>
   <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
   <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID></ModifyPartition>
   <ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
  </ModifyPartitions>
 </Disk></DiskConfiguration>
 <ImageInstall><OSImage>
  <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/INDEX</Key><Value>$($vm.Image)</Value></MetaData></InstallFrom>
  <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
 </OSImage></ImageInstall>
 <UserData><AcceptEula>true</AcceptEula><FullName>Lab</FullName><Organization>corp.example.com</Organization></UserData>
</component>
</settings>
<settings pass="specialize">
<component name="Microsoft-Windows-Shell-Setup" $x><ComputerName>$Name</ComputerName><TimeZone>UTC</TimeZone></component>
$net
$join
</settings>
<settings pass="oobeSystem">
<component name="Microsoft-Windows-International-Core" $x>$locale</component>
<component name="Microsoft-Windows-Shell-Setup" $x>
 <OOBE><HideEULAPage>true</HideEULAPage><HideLocalAccountScreen>true</HideLocalAccountScreen><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><ProtectYourPC>3</ProtectYourPC></OOBE>
 $accounts
 $logon
</component>
</settings>
</unattend>
"@

# --- config disc: autounattend.xml + C:\lab scripts ------------------------------
$stage = "$Lab\build\$Name"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\cfg\lab" | Out-Null
[IO.File]::WriteAllText("$stage\cfg\autounattend.xml", $unattend, (New-Object Text.UTF8Encoding $false))
$guest = "$Lab\guest\$($Name.ToLower())"
if (Test-Path $guest) {
    Copy-Item "$guest\*" "$stage\cfg\lab\" -Recurse
    @{ name = $Name; password = $pw } | ConvertTo-Json | Set-Content "$stage\cfg\lab\lab.json"
}

if (-not ('L8IsoWriter' -as [type])) {
    Add-Type -TypeDefinition @'
using System; using System.IO; using System.Runtime.InteropServices; using System.Runtime.InteropServices.ComTypes;
public static class L8IsoWriter {
  public static void Write(object image, string path) {
    var s = (IStream)image; var buf = new byte[1 << 16]; var n = Marshal.AllocHGlobal(4);
    try { using (var f = File.Create(path)) { while (true) { s.Read(buf, buf.Length, n); int r = Marshal.ReadInt32(n); if (r == 0) break; f.Write(buf, 0, r); } } }
    finally { Marshal.FreeHGlobal(n); }
  }
}
'@
}
$fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$fsi.FileSystemsToCreate = 3   # ISO 9660 + Joliet
$fsi.VolumeName = "L8CFG_$Name"
$fsi.Root.AddTree("$stage\cfg", $false)
[L8IsoWriter]::Write($fsi.CreateResultImage().ImageStream, "$stage\cfg.iso")

# --- VM ----------------------------------------------------------------------------
$dir = "$Lab\vms\$Name"
New-Item -ItemType Directory -Force $dir | Out-Null
New-VM -Name $Name -Generation 2 -MemoryStartupBytes 4GB -Path "$Lab\vms" -NewVHDPath "$dir\$Name.vhdx" -NewVHDSizeBytes 60GB -SwitchName L8Lab | Out-Null
Set-VM -Name $Name -ProcessorCount 2 -StaticMemory -AutomaticCheckpointsEnabled $false
Set-VMNetworkAdapter -VMName $Name -StaticMacAddress $vm.Mac
$dvd = Add-VMDvdDrive -VMName $Name -Path $vm.Iso -Passthru
Add-VMDvdDrive -VMName $Name -Path "$stage\cfg.iso"
# DVD first for the install; after that the prompt times out and the disk boots.
$hdd = Get-VMHardDiskDrive -VMName $Name
$nic = Get-VMNetworkAdapter -VMName $Name
Set-VMFirmware -VMName $Name -BootOrder $dvd, $hdd, $nic
Enable-VMIntegrationService -VMName $Name -Name 'Guest Service Interface'
Start-VM -Name $Name

# Gen 2 firmware asks "Press any key to boot from CD or DVD"; answer it for the first ~25 s.
$cs = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem -Filter "ElementName='$Name'"
$kb = Get-CimAssociatedInstance -InputObject $cs -ResultClassName Msvm_Keyboard
$until = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $until) {
    Invoke-CimMethod -InputObject $kb -MethodName TypeKey -Arguments @{ keyCode = [uint32]13 } | Out-Null
    Start-Sleep -Milliseconds 700
}
Write-Host "$Name is installing. Watch it in Hyper-V Manager, or wait for Wait-LabVM.ps1 -Name $Name."
