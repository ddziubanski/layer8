// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, type Branding, type User } from "../../api/client";
import { backgroundStyle, Brand, contrast, PRESETS, themeTokens, useBranding } from "../../branding";
import { useDropFiles } from "../../components/useDropFiles";
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

const isColour = (v: unknown): v is string => typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v);

/** How the console looks with these colours, light and (if it's on) dark, before saving. */
function ThemePreview({ values, name, logo }: { values: Record<string, unknown>; name: string; logo: string | null }) {
  const preset = typeof values.theme_preset === "string" && PRESETS[values.theme_preset] ? values.theme_preset : "custom";
  const fixed = PRESETS[preset];
  const brand = isColour(values.theme_brand) ? values.theme_brand : "#0f6b5c";
  const sidebar = isColour(values.theme_sidebar) ? values.theme_sidebar : "#14211e";
  const modes = fixed || values.dark_mode === "on" ? ["dark"] : values.dark_mode === "auto" ? ["light", "dark"] : ["light"];
  const checks = fixed ? [
    { label: fixed.button, ratio: contrast(fixed.brand, fixed.onBrand) },
    { label: "Panel text", ratio: contrast(fixed.ink, fixed.surface) },
    { label: "Secondary text", ratio: contrast(fixed.muted, fixed.surface) },
  ] : [
    { label: "Main colour", ratio: contrast(brand, "#ffffff") },
    { label: "Sidebar colour", ratio: contrast(sidebar, "#ffffff") },
  ];
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="lbl" style={{ margin: 0 }}>
        Preview
      </div>
      <div className="theme-previews">
        {modes.map((mode) => (
          <div key={mode} className="theme-preview" data-theme={mode} data-preset={preset} style={themeTokens(brand, sidebar, preset)} aria-label={`${mode === "dark" ? "Dark" : "Light"} preview`} role="img">
            <div className="side">
              <span className="brand">
                {logo && <img className="brand-logo" src={logo} alt="" />}
                {name}
              </span>
              <a className="active">Tickets</a>
              <a>Reports</a>
            </div>
            <div className="theme-preview-main">
              <div className="card stack" style={{ padding: 10, gap: 6 }}>
                <strong>Printer on 3rd floor jams</strong>
                <span>
                  <span className="chip g">Open</span> <span className="chip a">SLA at risk</span>
                </span>
                <a href="#preview" onClick={(e) => e.preventDefault()}>
                  View ticket
                </a>
              </div>
              <span className="btn">Send reply</span>
            </div>
          </div>
        ))}
      </div>
      {fixed && <p className="small muted">{fixed.about} Your custom colours and dark-mode preference are kept for when you switch back.</p>}
      <div className="contrast-note">
        {checks.map((c) => (
          <div key={c.label} className={c.ratio < 4.5 ? "error" : "muted"}>
            {c.label}: {fixed ? "text contrast" : "white text contrast"} {c.ratio.toFixed(1)}:1 {c.ratio < 4.5 ? "(too light; needs 4.5:1)" : "(readable)"}
          </div>
        ))}
      </div>
    </div>
  );
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
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["branding"] })}
        />
        <SettingsForm
          me={me}
          section="theme"
          title="Colours"
          embedded
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["branding"] })}
          preview={(values) => <ThemePreview values={values} name={brand.product_name} logo={brand.logo_url} />}
        />
        <SettingsForm me={me} section="signin_appearance" title="Sign-in appearance" embedded
          intro="Customize the main sign-in page. Upload a background below; keep your logo, welcome text and support message under Name and texts. These options also style password-change and two-factor setup screens, without changing authentication or portal appearance."
          onSaved={() => queryClient.invalidateQueries({queryKey:["branding"]})}
          preview={values => <SignInPreview brand={brand} values={values} />} />
        <ImageSlot name="background" title="Background" hint="Shown behind the sign-in page and the portal (and the console, if chosen above)." url={brand.background_url} />
        <ImageSlot name="logo" title="Logo" hint="Shown next to the product name." url={brand.logo_url} contain />
        <ImageSlot name="favicon" title="Favicon" hint="The small icon in the browser tab and bookmarks. A square image, 32×32 or larger." url={brand.favicon_url} contain types="ICO, PNG, GIF or JPG" />
      </div>
    </>
  );
}
