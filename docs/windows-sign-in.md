# Windows sign-in setup (portal single sign-on, Kerberos)

People who open the portal on a domain PC are signed in as their Windows user, with no password prompt. The app checks the browser's Kerberos ticket itself with a keytab. It doesn't need IIS, a proxy, or a domain-joined server.

It only signs in to the **portal**. The agent console always asks for a password.

Examples below use the portal address `helpdesk.corp.example` and the realm `CORP.EXAMPLE`.

> Check these steps against Microsoft's current documentation for `setspn`, `ktpass` and the Site to Zone Assignment List before running them. Cmdlet and policy details change.

## Before you start

- **DNS:** add an **A record** for `helpdesk.corp.example` that points at the server. Don't use a CNAME. Browsers ask for a ticket for the name the CNAME points to, and that name won't match the SPN.
- **Address:** users must open the portal by that name, e.g. `https://helpdesk.corp.example/`. An IP address or a short name like `http://helpdesk/` won't use Kerberos, and the user gets the password form instead.
- **Clock:** keep the server's clock in sync with the domain controllers, e.g. chrony pointed at the DCs. Kerberos refuses tickets when clocks are more than 5 minutes apart.
- **Network:** this works on the network or VPN only. Off-network users get the password form.

## 1. Service account

Create a dedicated account, not a person's. Its password protects the keytab.

```powershell
New-ADUser -Name "svc-helpdesk-portal" -SamAccountName "svc-helpdesk-portal" `
  -Description "Service Desk portal SSO (HTTP/helpdesk.corp.example)" `
  -AccountPassword (Read-Host -AsSecureString "Password") -Enabled $true `
  -PasswordNeverExpires $true -CannotChangePassword $true

# AES only. RC4 is deprecated, and the app refuses RC4-only keytabs.
Set-ADUser svc-helpdesk-portal -KerberosEncryptionType AES128,AES256
```

If the directory sync filter would pick this account up as a requester, keep it in an OU outside the synced base DN, or exclude it in the sync filter.

## 2. SPN

```powershell
setspn -Q HTTP/helpdesk.corp.example                     # must find nothing (no duplicates)
setspn -S HTTP/helpdesk.corp.example CORP\svc-helpdesk-portal
```

The SPN must exist on **one** account only. A duplicate SPN makes Kerberos fail for everyone.

## 3. Export the keytab

On a domain controller or an admin workstation with RSAT:

```powershell
ktpass /princ HTTP/helpdesk.corp.example@CORP.EXAMPLE /mapuser CORP\svc-helpdesk-portal `
  /crypto AES256-SHA1 /ptype KRB5_NT_PRINCIPAL /pass * /out helpdesk.keytab
```

- `/pass *` asks for a password and **sets it on the account**, so the keytab and the account always match. Use a long random password. Nobody needs to know it afterwards.
- `ktpass` also sets the account's UPN to the SPN. That's expected.
- Treat `helpdesk.keytab` like a password. Delete your copy once it's uploaded.

## 4. Upload it in the app

**Settings > Windows sign-in > Upload keytab.** The app checks the file and shows its service principal (`HTTP/helpdesk.corp.example@CORP.EXAMPLE`), the realm and the encryption types. It refuses files with no `HTTP/` principal, files mixing realms, and RC4-only files, and says why.

The keytab is stored on the server in `HELPDESK_SECRETS_DIR`, readable by the service only. It can't be downloaded again.

Tick **Offer "Sign in with your Windows account"**. Leave **Try it straight away** off until step 5 is in place.

## 5. Group Policy: trust the portal for Windows sign-in

Browsers only send a Kerberos ticket to sites they trust for it.

**Edge and Chrome on Windows** follow the Windows zone. In a GPO linked to the users' or computers' OU:

`Computer (or User) Configuration > Administrative Templates > Windows Components > Internet Explorer > Internet Control Panel > Security Page > Site to Zone Assignment List`

| Value name                  | Value |
|-----------------------------|-------|
| `https://helpdesk.corp.example`  | `1` (Local Intranet) |

If you'd rather not use zones, or want to be explicit, set the browser policies instead:

- Edge: `AuthServerAllowlist` = `helpdesk.corp.example`
- Chrome: `AuthServerAllowlist` = `helpdesk.corp.example`

**Firefox:** set `network.negotiate-auth.trusted-uris` to `https://helpdesk.corp.example` through `policies.json` or the Firefox GPO templates (Authentication > SPNEGO).

Once the GPO has applied, go back to **Settings > Windows sign-in** and tick **Try it straight away**. Opening the portal then signs people in with no click.

## Test it

On a domain PC, signed in as an ordinary user:

1. Open `https://helpdesk.corp.example/`. You should land in the portal as yourself.
2. In a command prompt, run `klist`. It should list a ticket for `HTTP/helpdesk.corp.example`, with `KerbTicket Encryption Type: AES-256-CTS-HMAC-SHA1-96`.

## Troubleshooting

| What you see | Likely cause |
|---|---|
| The browser asks for a username and password | The portal isn't trusted for Windows sign-in (GPO not applied yet). Run `gpupdate /force` and restart the browser. |
| "This computer didn't offer a Windows sign-in" | Same as above, or the portal was opened by IP address or short name, or the PC is off the network. |
| "Windows sign-in didn't work on this computer" | The ticket couldn't be checked: the keytab doesn't match the account (the password changed after export), a duplicate SPN exists, or clocks are out of sync. The API service log has the Kerberos error. |
| "Your Windows account isn't set up for the service desk" | The person isn't in the synced directory scope, is disabled in AD, or is from another realm. |
| `klist` shows no `HTTP/helpdesk.corp.example` ticket | Check `setspn -Q HTTP/helpdesk.corp.example` and the DNS A record. |

## Changing the account password or the host name

Changing the service account's password invalidates the keytab. Run step 3 again (it sets a new password) and upload the new keytab.

A new host name needs a new SPN (step 2), a new keytab (step 3), and a new GPO entry (step 5).

Removing the keytab in Settings turns Windows sign-in off for everyone straight away.
