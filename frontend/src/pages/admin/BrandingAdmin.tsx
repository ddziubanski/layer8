import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, type Branding, type User } from "../../api/client";
import { contrast, MIDNIGHT, themeTokens, useBranding } from "../../branding";
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
  const midnight = values.theme_preset === "midnight";
  const brand = isColour(values.theme_brand) ? values.theme_brand : "#0f6b5c";
  const sidebar = isColour(values.theme_sidebar) ? values.theme_sidebar : "#14211e";
  const modes = midnight || values.dark_mode === "on" ? ["dark"] : values.dark_mode === "auto" ? ["light", "dark"] : ["light"];
  const checks = midnight ? [
    { label: "Cyan button", ratio: contrast(MIDNIGHT.brand, MIDNIGHT.onBrand) },
    { label: "Panel text", ratio: contrast(MIDNIGHT.ink, MIDNIGHT.surface) },
    { label: "Secondary text", ratio: contrast(MIDNIGHT.muted, MIDNIGHT.surface) },
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
          <div key={mode} className="theme-preview" data-theme={mode} data-preset={midnight ? "midnight" : "custom"} style={themeTokens(brand, sidebar, midnight ? "midnight" : "custom")} aria-label={`${mode === "dark" ? "Dark" : "Light"} preview`} role="img">
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
      {midnight && <p className="small muted">Midnight pairs dark navy panels with cyan highlights. Your custom colours and dark-mode preference are kept for when you switch back.</p>}
      <div className="contrast-note">
        {checks.map((c) => (
          <div key={c.label} className={c.ratio < 4.5 ? "error" : "muted"}>
            {c.label}: {midnight ? "text contrast" : "white text contrast"} {c.ratio.toFixed(1)}:1 {c.ratio < 4.5 ? "(too light; needs 4.5:1)" : "(readable)"}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Settings > Branding: name, texts, colours and dark mode (with a live preview), background, logo and favicon. */
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
        <ImageSlot name="background" title="Background" hint="Shown behind the sign-in page and the portal (and the console, if chosen above)." url={brand.background_url} />
        <ImageSlot name="logo" title="Logo" hint="Shown next to the product name." url={brand.logo_url} contain />
        <ImageSlot name="favicon" title="Favicon" hint="The small icon in the browser tab and bookmarks. A square image, 32×32 or larger." url={brand.favicon_url} contain types="ICO, PNG, GIF or JPG" />
      </div>
    </>
  );
}
