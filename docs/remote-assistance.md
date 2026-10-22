# Remote Assistance from a ticket

A ticket's **Remote Assistance** button offers Windows Remote Assistance to the requester's computer (`msra /offerra <computer>`). The requester sees a prompt and accepts. The ticket notes who connected, to which computer, when, and for how long.

A browser can't start `msra.exe` itself. So each technician's PC gets a small launcher for `helpdesk-remote:` links, in `tools/remote-assistance/`.

## 1. Technicians' PCs: the launcher

As administrator, from `tools/remote-assistance/`:

```
.\install.ps1 -ServiceDeskUrl https://helpdesk.corp.example
```

This copies `helpdesk-remote.ps1` to `C:\Program Files\HelpdeskRemote\`. It records the service desk address in `HKLM\SOFTWARE\HelpdeskRemote\ServiceDeskUrl` and registers the `helpdesk-remote:` link type.

To deploy by GPO instead, copy the file and set the same registry values with Group Policy Preferences.

The launcher only accepts links for that service desk address, and only plain computer names.

Browsers ask before opening an external link. To skip the prompt for this link type only, set the Edge/Chrome policy **AutoLaunchProtocolsFromOrigins** to `[{"protocol": "helpdesk-remote", "allowed_origins": ["https://helpdesk.corp.example"]}]`.

## 2. Users' PCs: allow Remote Assistance offers

By GPO on the computers to be helped:

- **Computer Configuration > Administrative Templates > System > Remote Assistance > Configure Offer Remote Assistance:** Enabled, "Allow helpers to remotely control the computer". The helpers are your technicians' group, e.g. `CORP\SG-Helpdesk-Agents`.
- **Windows Defender Firewall:** enable the *Remote Assistance* rule group (domain profile). This includes the DCOM/RPC rules that `/offerra` needs.

The technician is then offered by name. The user still has to accept, and can end the session at any time.

## How the length is recorded

The button logs "Offered Remote Assistance to WS-0142" on the ticket. It then opens a link carrying a one-time token. When Remote Assistance closes, the launcher sends the token back, and the ticket notes "ended after 12 min".

If that report can't get through (no network, launcher missing), the technician can end the session on the ticket by hand.
