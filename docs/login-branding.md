# Main sign-in appearance

Admins can customize **Settings > Branding** without changing authentication or role routing.

- **Background, logo and favicon:** upload or drag images into the existing slots. Backgrounds and logos accept validated JPG/GIF/PNG, up to 8 MB. SVG/HTML uploads are not accepted. Images are public because sign-in shows them before authentication; use artwork suitable for public display.
- **Name and texts:** product name, existing sign-in support message, portal welcome and email footer retain their controls.
- **Sign-in appearance:** optional welcome heading, center/left/right card placement, standard/wide card, theme-default/ocean/network-grid backdrop, full-image/fill-screen fit, five image focal positions and 0–85% image darkening. Changes appear in a live preview before Save.
- **Colours:** existing themes, custom colours and dark-mode selection apply as before. Authentication cards and the source-code footer stay opaque for readability. Narrow screens center the card automatically.

The controls style sign-in, console password entry, required password changes and two-factor setup. The optional welcome heading appears on the main entry form. Portal backgrounds and console background opt-in retain their behavior. Local/AD/Entra/Windows sign-in, MFA, source-code links and server permissions cannot be hidden or changed by these controls. Welcome/support text is plain text; no custom HTML, scripts or CSS are accepted.

Defaults preserve the centered standard card, original image fit/focus and no image darkening. Remove the uploaded background and choose Theme default to return to the plain background. Image uploads remain shared with the portal; the new placement, fit, focal-point, overlay and backdrop controls affect sign-in only.

The API is `GET/PUT /api/settings/signin_appearance` (admin only); presentation values appear in public `GET /api/branding`. Installer/CLI answers use the `signin_appearance` section. Existing environment override conventions apply, for example `HELPDESK_SIGN_IN_LAYOUT=right`. No database migration is needed; settings use the existing settings store.
