# Public-exposure security review

What an outsider can reach on a Layer 8 server that is open to the internet, what was checked, and what is still open. Report security problems privately: see [SECURITY.md](../SECURITY.md).

| | |
|---|---|
| Reviewed | Layer 8 1.19.1 (`7d6e873`), 14 November 2026 |
| Kind | Source review plus targeted tests against the application. Not a penetration test. |
| Fixed in | 1.19.2: F1 to F4. 1.19.3: F5 to F7, and a mitigation for F8. |
| Verdict | No way in without credentials was found. With 1.19.3 and the checklist below, the weaknesses found here are closed, have a setting, or are accepted with the reason given. **An access gateway or VPN in front is still the recommended way to expose it**: this was a source review, not a penetration test, and most of the application sits behind a staff sign-in that this review did not cover. |

Layer 8 is built for an internal network: the same site holds the agent console, admin settings, the script runner and, on some installs, rights to change Active Directory. Prefer putting it behind something that checks identity first (a VPN, Entra application proxy or a similar access gateway).

## Scope

**Reviewed**

- Every route that answers without a signed-in user (30 of 448), read one by one.
- The 27 routes any signed-in user can call, requesters included, for access to other people's tickets, files and comments.
- Sign-in: passwords, lockout, two-factor, sessions, cookies, CSRF, the first-run admin, the development sign-in header.
- The nginx site the installer writes (`install/files/nginx-helpdesk.conf`) and how the API service is started.
- Known vulnerabilities in the shipped dependencies: the 52 Python wheels in the 1.19.1 bundle (`pip-audit`) and the frontend's runtime packages (`npm audit --omit=dev`).

**Not reviewed**

- The other 391 routes, which need an agent or admin. A staff account that is stolen or misused is outside this review; the October 2026 review covered script, queue and permission checks as they were then.
- Features added since that review beyond their sign-in checks: Action1 batch deployment, the Track-It!, KACE and Spiceworks imports, problem management, live updates.
- The operating system, the installer and ISO, backups, the updater, the relay (runner) program, and email intake.
- The frontend, beyond confirming it never renders raw HTML and that the content security policy blocks inline and third-party scripts.
- No scanner, fuzzer or load test was run, and nothing was tested from outside against a deployed appliance.

## What an outsider can reach

These 30 routes are the whole surface without a user session. `backend/tests/test_public_surface.py` pins the list: a new route without sign-in fails the test until it is reviewed and added here.

| Route | What guards it | Result |
|---|---|---|
| `GET /api/health`, `/api/about`, `/api/source` | Nothing; liveness, version and the AGPL source offer | OK; see F7 |
| `GET /api/openapi.json` (from 1.19.3) | Staff sign-in; not one of the 30 | OK |
| `GET /{path}` | Static files; the path must stay inside the frontend folder | OK |
| `POST /api/auth/login` | Argon2 passwords; 5 wrong tries lock an account for that address for 15 minutes; 30 failures per address per 15 minutes; one answer for every failure | OK; F1, F3 and F4 fixed in 1.19.2 |
| `POST /api/auth/logout`, `GET /api/auth/options`, `GET /api/branding`, `GET /api/branding/files/{name}` | Nothing sensitive; image names are fixed slots | OK |
| `POST /api/auth/sso` | A Kerberos ticket; 404 unless Windows sign-in is set up; the session is portal-only | OK |
| `GET /api/auth/entra/start`, `/callback` | Random state bound to the browser by a cookie; accounts matched by object ID, not the email claim | OK |
| `GET /api/live` | Checks the session itself, staff only, again every minute | OK |
| `GET/POST /api/integrations/tickets…` | API key (256-bit, stored hashed) or a staff session | OK |
| `GET /api/meraki/oauth/callback` | Hashed single-use state | OK |
| `POST /api/meraki/webhooks` | Shared secret of 32+ characters, constant-time compare; 503 unless configured; body and rate limits | OK |
| `POST /api/public/receiving/*` | Off by default from 1.19.3. PO number plus a receiving code (8 characters from 1.19.3); 10 wrong tries per address per 15 minutes | OK; F5 fixed in 1.19.3 |
| `GET /api/public/wallboard` | Bearer token (256-bit, stored hashed), revocable | OK |
| `POST /api/remote-sessions/{id}/end` | One-time token (stored hashed) | OK |
| `POST /api/runner/*` | Relay token (256-bit, stored hashed); a relay can only touch its own runs | OK |
| `GET/POST /api/surveys/answer/{token}` | 192-bit link token | OK |
| `GET /api/update-progress/{id}.json` | Unguessable ID; holds no secrets | OK |

Also checked and found sound:

- **Development sign-in header.** `X-Dev-User` works only when `HELPDESK_DEV_AUTH` is set in the environment; it is off by default, can't be turned on from Settings, and the installer never sets it. A test confirms the header is ignored.
- **Client address.** The API trusts `X-Forwarded-For` only from nginx on the same machine, and takes the address nginx saw, so an outsider can't fake an address to dodge the limits.
- **Cookies and CSRF.** The session cookie is HttpOnly, Secure and SameSite=Lax; sessions last 12 hours from last use and are stored hashed. Requests that change data need a custom header that another site can't send, and there is no CORS allowance.
- **Requesters.** A signed-in requester reaches only their own tickets, public comments and the files on them; internal notes and their files are staff-only; a requester can't post an internal note or attach to someone else's comment.
- **Directory look-ups.** Login names are escaped before they go into LDAP filters.
- **Uploaded files** are served as downloads or in a sandbox, never as pages of this site.
- **Port 80** only redirects to HTTPS; HTTPS sends HSTS.
- **Dependencies.** No known vulnerabilities in the shipped Python wheels or the frontend's runtime packages on the review date.

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | High | Anyone can make the server read a very large request before it checks who is asking | Fixed in 1.19.2 |
| F2 | High | Two-factor sign-in is optional, and can only be required of local admins | Setting added in 1.19.2; turn it on |
| F3 | Medium | An outsider who knows a login can keep that account locked out | Fixed in 1.19.2 |
| F4 | Medium | Sign-in and receiving limits are per worker process and forgotten on restart | Fixed in 1.19.2 |
| F5 | Medium | The no-sign-in receiving page is on by default | Fixed in 1.19.3 |
| F6 | Medium | A server exposed before setup is finished accepts `admin` / `admin` | Fixed in 1.19.3 |
| F7 | Low | The API description and exact version are public | Description fixed in 1.19.3; version accepted |
| F8 | Low | Up to 25 MB per attachment, unscanned, from any signed-in user | Mitigated in 1.19.3; scanning stays out of scope |

**F1. Large requests are read before sign-in is checked (high).** nginx accepts bodies up to 1 GB on every path, and the API reads and parses a JSON body before it looks at the session. In a test, a 200 MB body sent to `/api/auth/login` with no session was read in full and grew the process by about 800 MB before it was refused. A handful of such requests at once can exhaust the server's memory. *Fixed in 1.19.2:* the API refuses anything over 4 MB before reading it unless the path is an upload, and doesn't read an upload until the caller is signed in (`backend/app/bodylimit.py`). New installs also get an Nginx site that allows large requests only on the upload paths, unbuffered. **Servers installed before 1.19.2 keep their Nginx site**, which still accepts 1 GB on every path and stores it on disk before the API refuses it: copy the `client_max_body_size` line and the uploads `location` from `install/files/nginx-helpdesk.conf` into `/etc/nginx/sites-available/helpdesk`, then `nginx -t && systemctl reload nginx`.

**F2. Two-factor sign-in is optional (high for an internet-facing server).** Settings > Sign-in can require two-factor only of admins with a local password, and it is off by default. Agents and directory accounts sign in with a password alone, and a directory password that leaks elsewhere then opens the console. *In 1.19.2:* Settings > Sign-in has **Require two-factor sign-in for every agent and admin**, directory accounts included; sessions started through Microsoft are left to Entra's MFA and Conditional Access. It is off by default: turn it on before exposure.

**F3. Account lockout can be used against you (medium).** Five wrong passwords lock an account for 15 minutes, whoever sent them. Someone who knows an admin's email can keep them locked out, and the per-address limit of 30 failures still allows six lockouts per address per window. The recovery console still works. *Fixed in 1.19.2:* five wrong tries lock the account for that address only. Twenty wrong tries from anywhere within 15 minutes lock it for addresses it hasn't signed in from in the last 30 days, so the person can still sign in from where they usually do while guessing from elsewhere is stopped. Someone behind the same address as the victim (the same office) can still lock them out. Not done: an alert on repeated lockouts.

**F4. Limits live in memory, per worker (medium).** The API runs two worker processes, each counting failures on its own, and a restart clears the counts. The real limits are about double what the code says. *Fixed in 1.19.2:* counts are kept in the database (`sign_in_attempts`), shared by the workers and kept across restarts; IPv6 clients are counted by /64. Not done: `limit_req` in Nginx as a second layer.

**F5. Public receiving page on by default (medium).** `/receive` lets anyone with a PO number and its 6-character code (about 887 million possibilities) mark items received and upload a file of up to 15 MB. Guessing is limited per address, so a guess is unlikely to land, but PO numbers run in sequence and the page serves no purpose on the internet for most sites. *Fixed in 1.19.3:* the page is off unless turned on in Settings > Purchasing. An installation that already has purchase orders and never chose keeps it on, so printed receiving sheets go on working: **turn it off there if you don't use it.** New receiving codes are 8 characters (about 850 billion possibilities); older POs keep theirs.

**F6. First-run sign-in (medium).** Until a real admin exists, `admin` / `admin` signs in and must then set a password. Whoever reaches a fresh server first owns it. ISO installs set the admin during setup, so this concerns manual installs. Demo installs also carry a published demo sign-in and must never be exposed. *Fixed in 1.19.3:* first-run sign-in is accepted only from a loopback, private or link-local address, never from the internet. A server with only a public address is set up with `python -m app.cli create-admin` on the server itself. The demo sign-in is unchanged: never expose a demo install.

**F7. API description and version are public (low).** `/docs`, `/redoc` and `/openapi.json` answer without sign-in, and `/api/about` gives the exact version. The source is public anyway, but this tells an outsider precisely which release to look up. *In 1.19.3:* `/docs`, `/redoc` and `/openapi.json` are gone; the description is at `/api/openapi.json` for signed-in staff. *Accepted:* `/api/about` still gives the version without sign-in. Open browsers use it to notice an update and reload, the AGPL source offer sits beside it, and releases are public anyway.

**F8. Unscanned uploads (low; an accepted risk on an internal network).** Portal and agent uploads aren't scanned for malware. Internally that was accepted because email is scanned before delivery. With the portal on the internet, anyone with an account can hand staff a file. *In 1.19.3:* Settings > General has **File types requesters can't attach**: programs, scripts and disk images are refused by default when a requester uploads in the portal. Staff aren't limited. *Accepted:* files still aren't scanned; malware scanning is deliberately not part of Layer 8, so for an exposed portal scan at the gateway or on the workstations that open the files.

## Before exposing a server

1. Run 1.19.3 or later. Put an access gateway or VPN in front if you can.
1. On a server installed before 1.19.2, update the Nginx site by hand (see F1).
2. Use a certificate browsers trust, and open port 80 only together with 443.
3. Turn on **Require two-factor sign-in for every agent and admin** (Settings > Sign-in).
4. Check that the public receiving page is off (Settings > Purchasing) unless you need it.
5. Finish setup (a real admin, no demo data) before opening the firewall.
6. Keep the server updated from Settings > Updates, and keep backups off the server.

## Keeping this current

- `backend/tests/test_public_surface.py` fails when a route without sign-in is added or removed, and calls each one as an outsider with no or made-up credentials.
- Update the table above in the same change that adds such a route.
- Repeat the review, and the dependency audit, before any release that is meant to face the internet.
