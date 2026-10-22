# Recovery console

The appliance has a recovery account, `layer8`. Log in with it on the machine's screen or over SSH (`ssh layer8@helpdesk.corp.example`) and you get a menu, not a shell: the tasks needed to get Layer 8 back when something's wrong, and nothing else.

| Menu | What it does |
|---|---|
| **Network** | Show addresses, routes and DNS. Set a static address or use DHCP: the change undoes itself after 2 minutes unless you confirm it, so a typo over SSH can't lock you out. Set DNS servers and the search domain (kept even with DHCP). Set the host name. Ping, look up a name. |
| **Sign-in recovery** | List the admins and which can sign in with a local password. Reset a local admin's password. Make a break-glass local admin (keep its password in the safe). Sign everyone out. |
| **Services** | Are the web app, worker, web server and database running; does Layer 8 answer on HTTPS; restart one; read its recent log. |
| **HTTPS** | Make a new self-signed certificate for the server's name (browsers warn until it's replaced by one your PCs trust). |
| **Backups** | List backups, back up now. Restoring is a bigger step: see [install.md](install.md) (as `sdadmin`). |
| **Disk space and time** | Free space, clock and time sync. |
| **Full console** | A shell, after typing the `sdadmin` password. |
| **Restart / Shut down** | After a confirmation. |

## Keeping people in their lane

- `layer8`'s login shell is the menu (`/usr/local/sbin/layer8-console`). Over SSH it can only run the menu: no other command, no port forwarding, no file transfer (`/etc/ssh/sshd_config.d/layer8.conf`).
- Every privileged step goes through one helper, `/usr/local/sbin/layer8-recovery`, the only command `layer8` may run with sudo (`/etc/sudoers.d/layer8-recovery`). Its commands are fixed, and it checks every value (addresses, interface names, user names) before doing anything; nothing is passed to a shell.
- Knowing the `layer8` password isn't enough for a shell: the full console needs `sdadmin`'s.

## Passwords

The setup wizard asks for `layer8`'s password. Without one (an unattended install that doesn't set `install.recovery.password`), the account stays locked: there's no default password. Change it later as `sdadmin` with `sudo passwd layer8`.
