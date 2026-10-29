# SPDX-License-Identifier: AGPL-3.0-or-later
# DC01 after promotion (runs as SYSTEM at startup): DNS, DHCP, test people and groups that mirror
# dev/ad/seed.sh and computers.sh, plus what the relay kit needs (OU, group, gMSA, share).
# Ends by publishing TXT lab-ready.corp.example.com so the host can tell it's done.
$ErrorActionPreference = 'Stop'
Start-Transcript -Append C:\lab\stage2.log
for ($i = 0; $i -lt 60; $i++) { try { Get-ADDomain | Out-Null; break } catch { Start-Sleep 10 } }
Import-Module ActiveDirectory
$base = 'DC=corp,DC=example,DC=com'
function Exists($filter) { [bool](Get-ADObject -Filter $filter) }

# DNS: internet names through public resolvers; a reverse zone for the lab subnet.
Set-DnsServerForwarder -IPAddress 1.1.1.1, 8.8.8.8
# Promotion sometimes leaves the DNS server without the domain zones (event 708): add them and
# have Netlogon register the DC's SRV records, which domain joins need.
if (-not (Get-DnsServerZone -Name corp.example.com -ErrorAction SilentlyContinue)) {
    Add-DnsServerPrimaryZone -Name corp.example.com -ReplicationScope Domain -DynamicUpdate Secure
}
if (-not (Get-DnsServerZone -Name _msdcs.corp.example.com -ErrorAction SilentlyContinue)) {
    Add-DnsServerPrimaryZone -Name _msdcs.corp.example.com -ReplicationScope Forest -DynamicUpdate Secure
}
& ipconfig /registerdns | Out-Null
Restart-Service Netlogon
if (-not (Get-DnsServerZone -Name 0.88.10.in-addr.arpa -ErrorAction SilentlyContinue)) {
    Add-DnsServerPrimaryZone -NetworkId 10.88.0.0/24 -ReplicationScope Forest
}

# DHCP for the lab subnet only (10.88.0.100-199). Static hosts sit below .100.
& netsh dhcp add securitygroups | Out-Null
Restart-Service DHCPServer
if (-not (Get-DhcpServerInDC | Where-Object IPAddress -eq 10.88.0.10)) { Add-DhcpServerInDC -DnsName dc01.corp.example.com -IPAddress 10.88.0.10 }
if (-not (Get-DhcpServerv4Scope -ScopeId 10.88.0.0 -ErrorAction SilentlyContinue)) {
    Add-DhcpServerv4Scope -Name 'L8Lab' -StartRange 10.88.0.100 -EndRange 10.88.0.199 -SubnetMask 255.255.255.0 -LeaseDuration 1.00:00:00
}
Set-DhcpServerv4OptionValue -ScopeId 10.88.0.0 -Router 10.88.0.1 -DnsServer 10.88.0.10 -DnsDomain corp.example.com
Set-ItemProperty HKLM:\SOFTWARE\Microsoft\ServerManager\Roles\12 -Name ConfigurationState -Value 2

foreach ($ou in 'Workstations', 'Servers', 'Relays') {
    if (-not (Exists "distinguishedName -eq 'OU=$ou,$base'")) { New-ADOrganizationalUnit -Name $ou -Path $base -ProtectedFromAccidentalDeletion $false }
}

# People (same names and password as the Samba dev domain, so the live tests line up).
$pw = ConvertTo-SecureString 'Correct-horse-1' -AsPlainText -Force
$people = @(
    @{ Sam = 'jpark'; Given = 'J'; Sn = 'Park'; Display = 'J. Park'; Dept = 'IT'; Alias = 'j.park@example.com' },
    @{ Sam = 'dcole'; Given = 'D'; Sn = 'Cole'; Display = 'D. Cole' },
    @{ Sam = 'mosei'; Given = 'M'; Sn = 'Osei'; Display = 'M. Osei' },
    @{ Sam = 'ralvarez'; Given = 'R'; Sn = 'Alvarez'; Display = 'R. Alvarez'; Alias = 'frontdesk@corp.example.com' },
    @{ Sam = 'leaver'; Given = 'L'; Sn = 'Eaver' },
    @{ Sam = 'newhire'; Given = 'New'; Sn = 'Hire' }
)
foreach ($p in $people) {
    if (Exists "sAMAccountName -eq '$($p.Sam)'") { continue }
    $mail = "$($p.Sam)@corp.example.com"
    $display = if ($p.Display) { $p.Display } else { "$($p.Given) $($p.Sn)" }
    $proxy = @("SMTP:$mail") + @($p.Alias | Where-Object { $_ } | ForEach-Object { "smtp:$_" })
    $u = @{ Name = "$($p.Given) $($p.Sn)"; SamAccountName = $p.Sam; GivenName = $p.Given; Surname = $p.Sn; DisplayName = $display
            UserPrincipalName = $mail; EmailAddress = $mail; AccountPassword = $pw; Enabled = ($p.Sam -ne 'leaver')
            OtherAttributes = @{ proxyAddresses = [string[]]$proxy } }
    if ($p.Dept) { $u.Department = $p.Dept }
    New-ADUser @u
}
if (-not (Exists "sAMAccountName -eq 'svc-helpdesk'")) {
    New-ADUser -Name svc-helpdesk -SamAccountName svc-helpdesk -UserPrincipalName svc-helpdesk@corp.example.com -Description 'Helpdesk read-only bind' `
        -AccountPassword (ConvertTo-SecureString 'Svc-readonly-pass-1' -AsPlainText -Force) -PasswordNeverExpires $true -Enabled $true
}

function New-LabGroup($name, $category = 'Security', $sam = $name, $display = $name) {
    if (-not (Exists "sAMAccountName -eq '$sam'")) {
        New-ADGroup -Name $name -SamAccountName $sam -DisplayName $display -GroupScope Global -GroupCategory $category -Path "CN=Users,$base"
    }
}
'SG-Helpdesk-Admins', 'SG-Helpdesk-Agents', 'SG-IT-Leads', 'SG-Network-Team', 'M365-E3', 'SG-Layer8-Relays' | ForEach-Object { New-LabGroup $_ }
foreach ($dl in 'All Staff', 'IT', 'Network Alerts') { New-LabGroup "[EMAIL] $dl" Distribution ("EMAIL-$dl" -replace ' ', '-') "[EMAIL] $dl" }
Add-ADGroupMember SG-IT-Leads mosei
Add-ADGroupMember SG-Helpdesk-Admins SG-IT-Leads     # nested: IT leads are admins through the group
Add-ADGroupMember SG-Helpdesk-Agents jpark, dcole
Add-ADGroupMember SG-Network-Team dcole

# Computers: the two directory-sync fixtures, and pre-staged accounts for the lab machines.
if (-not (Exists "sAMAccountName -eq 'WS-0142$'")) {
    New-ADComputer WS-0142 -Path "OU=Workstations,$base" -Description 'Finance, 2nd floor' -DNSHostName ws-0142.corp.example.com `
        -ManagedBy (Get-ADUser ralvarez) -Enabled $true -OtherAttributes @{ operatingSystem = 'Windows 11 Enterprise'; operatingSystemVersion = '10.0 (22631)' }
}
if (-not (Exists "sAMAccountName -eq 'WS-0007$'")) { New-ADComputer WS-0007 -Path "OU=Workstations,$base" -Enabled $false }  # a retired PC
if (-not (Exists "sAMAccountName -eq 'RELAY01$'")) { New-ADComputer RELAY01 -Path "OU=Relays,$base" -Enabled $true }
if (-not (Exists "sAMAccountName -eq 'WS01$'")) { New-ADComputer WS01 -Path "OU=Workstations,$base" -Enabled $true }
Add-ADGroupMember SG-Layer8-Relays 'RELAY01$'

# gMSA for the relay task. Backdating the KDS key is fine in a one-DC lab.
if (-not (Get-KdsRootKey)) { Add-KdsRootKey -EffectiveTime ((Get-Date).AddHours(-10)) | Out-Null }
if (-not (Exists "sAMAccountName -eq 'gmsa-l8relay$'")) {
    New-ADServiceAccount gmsa-l8relay -DNSHostName gmsa-l8relay.corp.example.com -PrincipalsAllowedToRetrieveManagedPassword SG-Layer8-Relays
}

# Share the relay kit's GPO startup script reads from.
New-Item -ItemType Directory -Force C:\Shares\Layer8Relay | Out-Null
if (-not (Get-SmbShare -Name Layer8Relay -ErrorAction SilentlyContinue)) {
    New-SmbShare -Name Layer8Relay -Path C:\Shares\Layer8Relay -ReadAccess 'CORP\Domain Computers' -FullAccess 'CORP\Domain Admins' | Out-Null
}

# Portal single sign-on, as dev/ad/spn.sh: svc-portal (AES only) with SPN HTTP/helpdesk.corp.example.com,
# and its keytab in C:\lab\http.keytab for the app and tests/test_kerberos_live.py.
$spn = 'HTTP/helpdesk.corp.example.com'
if (-not (Exists "sAMAccountName -eq 'svc-portal'")) {
    New-ADUser -Name svc-portal -SamAccountName svc-portal -Description "Helpdesk portal SSO (SPN $spn)" -PasswordNeverExpires $true `
        -AccountPassword (ConvertTo-SecureString 'Kerb-Http-Lab-1' -AsPlainText -Force) -Enabled $true -KerberosEncryptionType AES256, AES128
    Set-ADUser svc-portal -ServicePrincipalNames @{ Add = $spn }
}
if (-not (Test-Path C:\lab\http.keytab)) {
    # ktpass resets the password, which also makes the AES keys. It reports success on stderr.
    $ErrorActionPreference = 'Continue'
    & ktpass /princ "$spn@CORP.EXAMPLE.COM" /mapuser CORP\svc-portal /pass 'Kerb-Http-Lab-2' /crypto AES256-SHA1 /ptype KRB5_NT_PRINCIPAL /out C:\lab\http.keytab 2>&1 | Out-Null
    $ErrorActionPreference = 'Stop'
}

# Enterprise root CA, so the DC enrolls a certificate and LDAPS works (Server 2025 refuses
# simple binds on plain LDAP). The CA certificate goes to C:\lab\corp-ca.cer for the app.
if (-not (Get-WindowsFeature ADCS-Cert-Authority).Installed) { Install-WindowsFeature ADCS-Cert-Authority -IncludeManagementTools | Out-Null }
if (-not (Get-Service CertSvc -ErrorAction SilentlyContinue) -or (Get-Service CertSvc).Status -ne 'Running') {
    Install-AdcsCertificationAuthority -CAType EnterpriseRootCA -CACommonName 'corp.example.com Lab Root CA' -KeyLength 2048 -HashAlgorithmName SHA256 -ValidityPeriod Years -ValidityPeriodUnits 10 -Force | Out-Null
}
for ($i = 0; $i -lt 30 -and -not (Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.1' }); $i++) { & certutil -pulse | Out-Null; Start-Sleep 10 }
Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like 'CN=corp.example.com Lab Root CA*' | Select-Object -First 1 | Export-Certificate -FilePath C:\lab\corp-ca.cer -Type CERT | Out-Null

# Done: drop the password copy, stop re-running, tell the host.
Remove-Item C:\lab\lab.json -Force -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName L8LabStage2 -Confirm:$false
if (-not (Get-DnsServerResourceRecord -ZoneName corp.example.com -Name lab-ready -ErrorAction SilentlyContinue)) {
    Add-DnsServerResourceRecord -ZoneName corp.example.com -Name lab-ready -Txt -DescriptiveText ("DC01 ready " + (Get-Date -Format o))
}
