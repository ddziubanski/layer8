# Windows AD test lab

The Samba domain controller in `dev/ad` (`docker compose --profile ad up -d ad`) covers directory sync, role mappings and portal sign-on, and needs nothing but Docker. Some things need real Windows: Group Policy, gMSAs, PowerShell running as SYSTEM, Windows DHCP and DNS, and the stricter defaults of Windows Server 2025. For those, `dev/ad-lab/` builds a small Hyper-V lab without anyone clicking through an installer.

It's a disposable test domain: one password for the whole lab, kept in a plain file. Never connect it to a real network or reuse its names elsewhere.

## What you get

| VM | Address | What's on it |
|---|---|---|
| DC01, Windows Server 2025 | 10.88.0.10 | Domain controller for `corp.example.com` (NetBIOS `CORP`): DNS, DHCP (10.88.0.100–199), an enterprise root CA (so LDAPS works), the people, groups and computers from `dev/ad/seed.sh`, a `svc-portal` account with SPN `HTTP/helpdesk.corp.example.com` and its keytab, plus what relays need: OU `Relays`, group `SG-Layer8-Relays`, gMSA `gmsa-l8relay` and share `\\DC01\Layer8Relay` |
| RELAY01, Windows Server 2025 | 10.88.0.20 | Member server in OU `Relays` and group `SG-Layer8-Relays`, with Python 3.12 |
| WS01, Windows 11 Enterprise | DHCP | Workstation in OU `Workstations`, for checking that relay policy *doesn't* reach it |

All three sit on a private Hyper-V switch, `L8Lab` (10.88.0.0/24), with outbound internet access through NAT. Your real network can't reach them.

The domain is `corp.example.com`, not `corp.example`: Windows Server 2025 DNS refuses to host any zone under `.example`.

## Needs

- Windows 10/11 Pro or Enterprise with Hyper-V, about 16 GB of free RAM and 200 GB of disk.
- Evaluation ISOs from the Microsoft Evaluation Center, saved in `C:\isos` under the names Microsoft gives them: Windows Server 2025 (`*SERVER_EVAL*.iso`) and, for WS01, Windows 11 Enterprise (`*CLIENTENTERPRISEEVAL*.iso`).

## Build it

1. Copy `dev/ad-lab` to `C:\layer8-lab` (from WSL: `cp -r dev/ad-lab/. /mnt/c/layer8-lab/`).
2. In an admin PowerShell, run `1-enable-hyperv.ps1` (it reboots), then `2-lab-network.ps1`. If you work in WSL, also run `3-wsl-routing.ps1` (lets WSL reach the lab) and `4-lab-dns.ps1` (sends `*.corp.example.com` lookups to DC01).
3. As yourself (admin rights aren't needed after step 1), build the VMs one at a time:

   ```powershell
   cd C:\layer8-lab
   .\Build-LabVM.ps1 -Name DC01;    .\Wait-LabVM.ps1 -Name DC01
   .\Build-LabVM.ps1 -Name RELAY01; .\Wait-LabVM.ps1 -Name RELAY01
   .\Build-LabVM.ps1 -Name WS01;    .\Wait-LabVM.ps1 -Name WS01
   ```

   Each takes 15–30 minutes. `Get-LabScreen.ps1 -Name DC01` saves a screenshot of a VM's console to `build\`.
4. Take a checkpoint of each VM (`Checkpoint-VM -Name DC01 -SnapshotName built`) before testing anything that changes it.

What you'll find in `C:\layer8-lab` afterwards:

- `secrets\lab-admin-password.txt`: the password for `CORP\Administrator` (domain admin), `RELAY01\Administrator` and `WS01\labadmin`.
- `corp-ca.pem`: the lab CA certificate, for LDAPS.
- `http.keytab`: the portal's keytab, for Windows sign-in.

Test users have the same password as in the Samba domain (`Correct-horse-1`), and the bind account is `CN=svc-helpdesk,CN=Users,DC=corp,DC=example,DC=com` with password `Svc-readonly-pass-1`.

From the host you can run commands inside a VM with PowerShell Direct, which works without a network path:

```powershell
Invoke-Command -VMName DC01 -Credential CORP\Administrator -ScriptBlock { Get-ADUser -Filter * | Select Name }
```

## Run the live tests against it

```sh
cd backend
HELPDESK_TEST_LDAP=1 HELPDESK_TEST_AD=lab .venv/bin/pytest tests/test_ldap_live.py tests/test_kerberos_live.py
```

`tests/live_ad.py` holds both test domains. The CA and keytab paths default to `/mnt/c/layer8-lab`. Set `HELPDESK_TEST_AD_CA` and `HELPDESK_TEST_AD_KEYTAB` to use files somewhere else.

## Windows Server 2025 compared with Samba

- **LDAPS is required.** Server 2025 refuses simple binds over plain LDAP (`strongerAuthRequired`). Layer 8 connects with LDAPS anyway. Point it at the DC by name (`ldaps://dc01.corp.example.com`) so the certificate matches, and give it the domain's CA.
- **Some attributes can't be written.** Samba lets the seed set `lastLogonTimestamp`; Windows doesn't. In the lab, WS-0142 has never signed in.
- **Password rules are enforced.** A password can't contain the account name. That's why `svc-portal` has a different password here than in Samba.
