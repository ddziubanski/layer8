// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type CSSProperties } from "react";

import { api, type Branding } from "./api/client";

export const DEFAULT_ICON = "/layer8.svg";
export const DEFAULT_LOGO = "/layer8-mark.png";

export const DEFAULT_LIGHT_THEME = {
  theme_preset: "custom",
  theme_brand: "#0f6b5c",
  theme_sidebar: "#14211e",
  dark_mode: "off",
} as const;

const DEFAULT: Branding = {
  product_name: "Layer 8",
  console_theme: "tidepool",
  sign_in_message: "",
  sign_in_heading: "",
  sign_in_layout: "center",
  sign_in_card_size: "standard",
  sign_in_background_fit: "cover",
  sign_in_background_position: "center",
  sign_in_background_overlay: 0,
  sign_in_backdrop: "plain",
  background_in_console: false,
  portal_welcome: "",
  ...DEFAULT_LIGHT_THEME,
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

/** A fixed dark palette (styles.css has the rest of each one's tokens under data-preset). */
export type Preset = {
  label: string; button: string; about: string;
  brand: string; onBrand: string; brandDark: string; accent: string; link: string;
  sidebar: string; sideOn: string; sideInk: string; ink: string; muted: string; surface: string;
  greenBg: string; green: string; amberBg: string; amber: string; redBg: string; red: string;
};

export const MIDNIGHT: Preset = {
  label: "Midnight", button: "Cyan button", about: "Midnight pairs dark navy panels with cyan highlights.",
  brand: "#00b7d6", onBrand: "#031b28", brandDark: "#36d1e9", accent: "#26d8f0", link: "#55dbf3",
  sidebar: "#07131f", sideOn: "#102d43", sideInk: "#acc2d5", ink: "#e6f0fa", muted: "#acc2d5", surface: "#101f2d",
  greenBg: "#134d41", green: "#a0f4d2", amberBg: "#624600", amber: "#ffe1a2", redBg: "#702932", red: "#ffe3e6",
};

export const EMBER: Preset = {
  label: "Ember", button: "Violet button", about: "Ember pairs dark plum panels with violet buttons and amber highlights.",
  brand: "#7c4dff", onBrand: "#ffffff", brandDark: "#9670ff", accent: "#ffb547", link: "#ffb547",
  sidebar: "#1a0f2e", sideOn: "#2a1a45", sideInk: "#cdbfe2", ink: "#f3ecfb", muted: "#b9a8cf", surface: "#1b1228",
  greenBg: "#0f3d3a", green: "#9ff0dc", amberBg: "#4a3510", amber: "#ffe08a", redBg: "#4a1424", red: "#ffc2cc",
};

export const ULTRAVIOLET: Preset = {
  label: "Ultraviolet", button: "Violet button", about: "Ultraviolet is flat near-black with one violet and gold highlights.",
  brand: "#7c3aed", onBrand: "#ffffff", brandDark: "#9461f2", accent: "#facc15", link: "#facc15",
  sidebar: "#121019", sideOn: "#1f1b2c", sideInk: "#c4c0d4", ink: "#ecebf3", muted: "#a9a5bb", surface: "#16141f",
  greenBg: "#0f3326", green: "#a7f3d0", amberBg: "#3b3008", amber: "#fde68a", redBg: "#3d1418", red: "#ffb4b4",
};

export const PRESETS: Record<string, Preset> = { midnight: MIDNIGHT, ember: EMBER, ultraviolet: ULTRAVIOLET };

/** The CSS tokens a theme sets (see styles.css), for the page or a preview box. */
export function themeTokens(brand: string, sidebar: string, preset = "custom"): Record<string, string> {
  const fixed = PRESETS[preset];
  if (fixed) return {
    "--brand": fixed.brand, "--on-brand": fixed.onBrand, "--brand-dark": fixed.brandDark,
    "--side": fixed.sidebar, "--side-on": fixed.sideOn, "--side-ink": fixed.sideInk,
    "--accent": fixed.accent, "--dark-link": fixed.link,
  };
  const light = lightOn(brand, DARK_SURFACE, 4.5);
  return {
    "--brand": brand,
    "--on-brand": "#ffffff",
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
  const customDark = useDark(b.dark_mode);
  const dark = b.theme_preset in PRESETS || customDark;
  useEffect(() => {
    document.title = b.product_name;
  }, [b.product_name]);
  useEffect(() => {
    const root = document.documentElement;
    for (const [k, v] of Object.entries(themeTokens(b.theme_brand, b.theme_sidebar, b.theme_preset))) root.style.setProperty(k, v);
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.preset = b.theme_preset ?? "custom";
  }, [b.theme_brand, b.theme_sidebar, b.theme_preset, dark]);
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
  if (where === "sign-in") {
    const positions = {center:"center",top:"center top",bottom:"center bottom",left:"left center",right:"right center"};
    const backdrop = b.sign_in_backdrop === "ocean" ? "radial-gradient(ellipse at top left, #135d6a, #071421 75%)" : b.sign_in_backdrop === "grid" ? "repeating-linear-gradient(0deg, #238c9826 0 1px, transparent 1px 36px), repeating-linear-gradient(90deg, #238c9826 0 1px, transparent 1px 36px), linear-gradient(#071421, #102b3b)" : "none";
    const alpha = Math.max(0, Math.min(85, Number(b.sign_in_background_overlay) || 0)) / 100;
    return {
      backgroundImage: b.background_url ? `linear-gradient(rgb(0 0 0 / ${alpha}), rgb(0 0 0 / ${alpha})), url("${b.background_url}"), ${backdrop}` : backdrop,
      backgroundSize: b.background_url ? `100% 100%, ${b.sign_in_background_fit === "contain" ? "contain" : "cover"}, auto` : "cover",
      backgroundPosition: b.background_url ? `center, ${positions[b.sign_in_background_position ?? "center"] ?? "center"}, center` : "center",
      backgroundRepeat: "no-repeat",
      backgroundAttachment: "fixed",
      "--login-align": b.sign_in_layout === "left" ? "start" : b.sign_in_layout === "right" ? "end" : "center",
      "--login-width": b.sign_in_card_size === "wide" ? "480px" : "380px",
    } as CSSProperties;
  }
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
