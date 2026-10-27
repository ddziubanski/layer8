// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, type Branding, type User } from "../../api/client";
import { backgroundStyle, Brand, useBranding } from "../../branding";
import { useDropFiles } from "../../components/useDropFiles";
import { materialTheme, materialTokens } from "../../materialThemes";
import { SettingsForm } from "./SettingsForm";

/** One uploadable image: preview, choose or drag a file, remove. */
function ImageSlot({
  name,
  title,
  hint,
  url,
  contain = false,
  types = "JPG, GIF or PNG",
}: {
  name: "background" | "logo" | "favicon";
  title: string;
  types?: string; hint: string; url: string | null; contain?: boolean }) {
  const queryClient = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const done = (b: Branding) => queryClient.setQueryData(["branding"], b);
  const upload = useMutation({ mutationFn: (file: File) => api.uploadBrandingImage(name, file), onSuccess: done });
  const remove = useMutation({ mutationFn: () => api.removeBrandingImage(name), onSuccess: done });
  const drop = useDropFiles((files) => upload.mutate(files[0]), { disabled: upload.isPending });
  const error = upload.error ?? remove.error;

  return (
    <section className="card stack settings-form" style={{ margin: 0 }} aria-label={title}>
      <div className="lbl">{title}</div>
      <div className={`image-slot${drop.dragging ? " dropping" : ""}`} {...drop.props}>
        <div className={`thumb${contain ? " contain" : ""}`} style={url ? { backgroundImage: `url("${url}")` } : undefined} role="img" aria-label={url ? `Current ${title.toLowerCase()}` : `No ${title.toLowerCase()}`}>
          {!url && "None"}
        </div>
        <div className="stack" style={{ gap: 6 }}>
          <span className="small">{hint}</span>
          <span className="muted small">{types}, up to 8 MB. Choose a file or drag it here.</span>
          <div className="row-gap">
            <input
              ref={picker}
              type="file"
              accept={name === "favicon" ? "image/jpeg,image/gif,image/png,image/x-icon,.ico" : "image/jpeg,image/gif,image/png"}
              hidden
              aria-label={`Upload ${title.toLowerCase()}`}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
            <button className="btn2" type="button" disabled={upload.isPending} onClick={() => picker.current?.click()}>
              {url ? "Replace…" : "Choose image…"}
            </button>
            {url && (
              <button className="linkish small" type="button" disabled={remove.isPending} onClick={() => remove.mutate()}>
                Remove
              </button>
            )}
          </div>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
    </section>
  );
}

/** Both supported appearances of the organization palette. */
function ThemePreview({ values, name }: { values: Record<string, unknown>; name: string; logo: string | null }) {
  return <div className="theme-previews">{[false, true].map(dark => <div key={String(dark)} className="material-theme-preview" style={materialTokens(materialTheme(values.console_theme), dark)} aria-label={`${dark ? "Dark" : "Light"} preview`} role="img"><strong>{name}</strong><span className="small muted">{dark ? "Dark" : "Light"} · Ticket Focus</span><h3>VPN connection failing</h3><p>Conversation · Details · Tools</p><span className="btn">Send reply</span></div>)}</div>;
}

export function SignInPreview({ brand, values }: { brand: Branding; values: Record<string, unknown> }) {
  const preview = {...brand, ...values, sign_in_background_overlay: Number(values.sign_in_background_overlay ?? brand.sign_in_background_overlay ?? 0)} as Branding;
  return <section className="login login-preview" style={{...backgroundStyle(preview,"sign-in"),backgroundAttachment:"scroll"}} role="img" aria-label="Sign-in appearance preview">
    <div className="card login-card">
      <p className="workspace-area">SERVICE DESK ACCESS</p>
      <h2><Brand b={preview} className="" /></h2>
      {preview.sign_in_heading && <h3 className="sign-in-heading">{preview.sign_in_heading}</h3>}
      <p className="muted">Your existing access opens the helpdesk or self-service portal.</p>
      <span className="small">Email or username</span><div className="login-preview-field">technician@example.com</div>
      <span className="small">Password</span><div className="login-preview-field">••••••••••••</div>
      <span className="btn login-preview-button">Sign in</span>
      {preview.sign_in_message && <p className="muted small">{preview.sign_in_message}</p>}
    </div>
    <p className="small login-source">Appearance preview · sign-in options follow your existing configuration</p>
  </section>;
}

/** Settings > Branding: name, texts, colours, sign-in appearance and uploaded images. */
export function BrandingAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const brand = useBranding();
  if (me.role !== "admin") return <p>Branding is managed by admins.</p>;
  return (
    <>
      <div className="page-head">
        <h1>Branding</h1>
      </div>
      <div className="stack">
        <SettingsForm
          me={me}
          section="branding"
          title="Name and texts"
          embedded
          preview={values => <SignInPreview brand={brand} values={values} />}
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["branding"] })}
        />
        <SettingsForm
          me={me}
          section="theme"
          title="Colours"
          restoreDefaults={{ label: "Restore default light theme", values: { console_theme: "tidepool", dark_mode: "off" } }}
          visibleFields={["console_theme", "dark_mode"]}
          embedded
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["branding"] })}
          preview={(values) => <ThemePreview values={values} name={brand.hide_product_name ? "" : brand.product_name} logo={brand.logo_url} />}
        />
        <SettingsForm me={me} section="signin_appearance" title="Sign-in appearance" embedded
          intro="Customize the main sign-in page. Product name and support message are under Name and texts; upload a background or logo below. These options also style password-change and two-factor setup screens, without changing authentication or portal appearance."
          onSaved={() => queryClient.invalidateQueries({queryKey:["branding"]})}
          preview={values => <SignInPreview brand={brand} values={values} />} />
        <ImageSlot name="background" title="Background" hint="Shown behind the sign-in page and the portal (and the console, if chosen above)." url={brand.background_url} />
        <ImageSlot name="logo" title="Logo" hint="Shown with the optional product name. Use Name and texts above to hide the name or center the logo." url={brand.logo_url} contain />
        <ImageSlot name="favicon" title="Favicon" hint="The small icon in the browser tab and bookmarks. A square image, 32×32 or larger." url={brand.favicon_url} contain types="ICO, PNG, GIF or JPG" />
      </div>
    </>
  );
}
