# Sign-in: two-factor and Microsoft Entra ID

## Two-factor sign-in (authenticator app)

Anyone who signs in with a password (a local account, or an AD account through the directory) can turn on two-factor sign-in: **My preferences > Two-factor sign-in > Turn on**. Scan the QR code with an authenticator app (Microsoft Authenticator, Google Authenticator, or any app for TOTP codes), type the code it shows, and keep the eight recovery codes somewhere safe. From then on, sign-in asks for a code after the password.

- Each code works once. A recovery code also works once, if the phone is lost.
- The secret is encrypted on the server with a key in the secrets folder (`totp.key`) and is never shown again.
- Turning it off needs the password and a code.
- **Settings > Sign-in > Require two-factor sign-in for admins with a local password**: those admins set it up at their next sign-in, before they can do anything else, and can't turn it off. This protects the break-glass admin.

**A lost phone:**
- An admin opens **Settings > Users** and chooses **Reset two-factor** for that person. That person is signed out everywhere and sets it up again.
- If the only admin is locked out, on the server run `python -m app.cli two-factor-reset <email>` as the service account.

Windows sign-in to the portal and Sign in with Microsoft don't ask for a Layer 8 code. Windows and Microsoft Entra already did the checking.

## Sign in with Microsoft (Entra ID)

The sign-in page offers **Sign in with Microsoft**. It uses OpenID Connect (authorization code flow with PKCE), so Entra's own MFA and Conditional Access apply.

**Who can use it:**
- Only people who already have an account in Layer 8. They're matched by their Entra object ID once linked, or the first time by UPN or email.
- Nobody is created by signing in. People come from AD sync, or an admin adds them.
- Accounts with a local password can't use it, so the break-glass admin always signs in locally.

### 1. App registration

In the Microsoft Entra admin center, create a **new** app registration for sign-in (keep it separate from the mail and Teams registrations):

1. **App registrations > New registration.** Name it *Layer 8 sign-in*. Supported account types: *this organizational directory only*.
2. **Redirect URI:** platform *Web*, `https://<your portal address>/api/auth/entra/callback` (Settings > General > Portal address must be that HTTPS address).
3. **Certificates & secrets > New client secret.** Copy its value.
4. **API permissions:** the default delegated `User.Read` is all it needs. Grant admin consent so people aren't asked.
5. Optional: **Enterprise applications > Layer 8 sign-in > Properties > Assignment required** = Yes, then assign the groups allowed to use it.

### 2. Layer 8

**Settings > Sign-in:** tick **Sign in with Microsoft**, and fill in the **Tenant ID**, **Application (client) ID** and **Client secret**. The secret is stored as a file and never shown back. Save, sign out, and the button appears on the sign-in page.

All of it can go in an answers file under `signin`, and `HELPDESK_ENTRA_*` / `HELPDESK_TOTP_REQUIRED_ADMINS` environment variables override it.

### What's checked

- The attempt is bound to the browser that started it (a short-lived cookie) and to a random state, and expires after 10 minutes. It can only be used once.
- MSAL checks the state and the nonce, and exchanges the code directly with Microsoft.
- The account must be in your tenant, and the Layer 8 account must be active. An account already linked to a different Microsoft account is refused.
- Refusals show on the sign-in page and in Settings > Log.
