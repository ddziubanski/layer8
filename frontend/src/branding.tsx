import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type CSSProperties } from "react";

import { api, type Branding } from "./api/client";

export const DEFAULT_ICON = "/layer8.svg";
export const DEFAULT_LOGO = "/layer8-mark.png";

const DEFAULT: Branding = {
  product_name: "Layer 8",
  sign_in_message: "",
  background_in_console: false,
  portal_welcome: "",
  theme_brand: "#0f6b5c",
  theme_sidebar: "#14211e",
  dark_mode: "off",
  background_url: null,
  logo_url: null,
  favicon_url: null,
};

// Colour sums for the theme: #rrggbb in, #rrggbb out.
type Rgb = [number, number, number];
const rgb = (hex: string): Rgb => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb;
const hex = (c: Rgb) => `#${c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;
/** a moved towards b by t (0 to 1). */
export const mix = (a: string, b: string, t: number) => hex(rgb(a).map((v, i) => v + (rgb(b)[i] - v) * t) as Rgb);

function luminance(c: string): number {
  const [r, g, b] = rgb(c).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio (1 to 21); the server checks the same 4.5:1 for white text. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The brand colour lightened just enough to read on a dark background (links in dark mode, the sidebar marker). */
function lightOn(colour: string, background: string, ratio: number): string {
  for (let t = 0; t <= 1; t += 0.05) {
    const c = mix(colour, "#ffffff", t);
    if (contrast(c, background) >= ratio) return c;
  }
  return "#ffffff";
}

const DARK_SURFACE = "#1a2321";

/** The CSS tokens a theme sets (see styles.css), for the page or a preview box. */
export function themeTokens(brand: string, sidebar: string): Record<string, string> {
  const light = lightOn(brand, DARK_SURFACE, 4.5);
  return {
    "--brand": brand,
    "--brand-dark": mix(brand, "#000000", 0.3),
    "--side": sidebar,
    "--side-on": mix(sidebar, "#ffffff", 0.08),
    "--side-ink": mix(sidebar, "#ffffff", 0.82),
    "--accent": lightOn(brand, sidebar, 3),
    "--dark-link": light,
  };
}

/** Whether dark mode is on for this person: always, never, or as their device says. */
function useDark(mode: string): boolean {
  const [query] = useState(() => (window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null));
  const [prefers, setPrefers] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const on = (e: MediaQueryListEvent) => setPrefers(e.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, [query]);
  return mode === "on" || (mode === "auto" && prefers);
}

/** Settings > Branding, readable before sign-in. Also keeps the tab's title, favicon, colours and dark mode in step. */
export function useBranding(): Branding {
  const q = useQuery({ queryKey: ["branding"], queryFn: api.branding, staleTime: 5 * 60_000 });
  const b = { ...DEFAULT, ...q.data };
  const dark = useDark(b.dark_mode);
  useEffect(() => {
    document.title = b.product_name;
  }, [b.product_name]);
  useEffect(() => {
    const root = document.documentElement;
    for (const [k, v] of Object.entries(themeTokens(b.theme_brand, b.theme_sidebar))) root.style.setProperty(k, v);
    root.dataset.theme = dark ? "dark" : "light";
  }, [b.theme_brand, b.theme_sidebar, dark]);
  useEffect(() => {
    let link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    // An uploaded favicon (Settings > Branding), else the Layer 8 mark.
    link.href = b.favicon_url ?? DEFAULT_ICON;
    link.type = b.favicon_url ? "" : "image/svg+xml";
  }, [b.favicon_url]);
  return b;
}

/** The uploaded background behind a page, or nothing. */
export function backgroundStyle(b: Branding, where: "sign-in" | "portal" | "console"): CSSProperties | undefined {
  if (!b.background_url || (where === "console" && !b.background_in_console)) return undefined;
  return { backgroundImage: `url("${b.background_url}")`, backgroundSize: "cover", backgroundPosition: "center", backgroundAttachment: "fixed" };
}

/** Logo (if uploaded) and product name. */
export function Brand({ b, className = "brand" }: { b: Branding; className?: string }) {
  return (
    <span className={className}>
      <img className={`brand-logo${b.logo_url ? "" : " brand-logo-default"}`} src={b.logo_url ?? DEFAULT_LOGO} alt="" />
      {b.product_name}
    </span>
  );
}
