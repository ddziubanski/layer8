// SPDX-License-Identifier: AGPL-3.0-or-later
import type { CSSProperties } from "react";
import type { Branding, Me } from "./api/client";

export const MATERIAL_THEMES = {
  tidepool: { label: "Tidepool", light: ["#edf5f2", "#f8fdfb", "#e4efeb", "#d9e8e3", "#172522", "#52655f", "#bfcec8", "#006b61", "#ffffff", "#9ff2df", "#00382f"], dark: ["#101b18", "#172620", "#20302a", "#2b3c35", "#e2eee7", "#b9ccc1", "#40584b", "#79d8c5", "#00382f", "#135047", "#aff4e3"] },
  amethyst: { label: "Amethyst", light: ["#f5f2fb", "#fffbff", "#eeebf5", "#e7e1ef", "#211c2b", "#625b70", "#d1c9dc", "#6547a9", "#ffffff", "#e9ddff", "#32155f"], dark: ["#17131e", "#211c29", "#292331", "#342d3d", "#eee6f5", "#cbc0d6", "#4e435b", "#d0bcff", "#361b65", "#4d347e", "#edddff"] },
  "blue-steel": { label: "Blue Steel", light: ["#f0f4fb", "#fbfcff", "#e8eef8", "#dfe7f4", "#1b2432", "#596576", "#c6d0e0", "#2b59a5", "#ffffff", "#d6e3ff", "#0a2e63"], dark: ["#111923", "#192330", "#222e3e", "#2e3a4c", "#e6edf7", "#becadb", "#45546a", "#abc7ff", "#083065", "#28487d", "#d9e5ff"] },
  terracotta: { label: "Terracotta", light: ["#fcf1eb", "#fffbf8", "#f6e9df", "#efdfd2", "#302019", "#746056", "#dfc7b7", "#98432f", "#ffffff", "#ffdbca", "#52200e"], dark: ["#211711", "#2e211a", "#3a2b22", "#48362c", "#f8e7db", "#d8bfb0", "#654b3c", "#ffb59b", "#58220f", "#763d2a", "#ffdbca"] },
  "graphite-lime": { label: "Graphite Lime", light: ["#f2f4ec", "#fbfcf6", "#e9eddf", "#e0e5d5", "#24291c", "#5d6552", "#c9d0ba", "#4e681e", "#ffffff", "#d2eca7", "#243800"], dark: ["#171a13", "#20251b", "#2b3024", "#363e2c", "#e9eddf", "#c4cbb8", "#505b42", "#b1d77c", "#263e00", "#3c5420", "#d2eca7"] },
} as const;
export type MaterialTheme = keyof typeof MATERIAL_THEMES;
export const DEFAULT_APPEARANCE = { palette: null, mode: "organization" } as const;
export function materialTheme(value: unknown): MaterialTheme {
  return typeof value === "string" && Object.hasOwn(MATERIAL_THEMES, value) ? value as MaterialTheme : "tidepool";
}
export function consoleAppearance(me: Me, brand: Branding, prefersDark: boolean) {
  const personal = me.console_appearance ?? DEFAULT_APPEARANCE;
  const mode = personal.mode === "organization" ? brand.dark_mode === "on" ? "dark" : brand.dark_mode === "auto" ? "system" : "light" : personal.mode;
  return { palette: materialTheme(personal.palette ?? brand.console_theme), dark: mode === "dark" || (mode === "system" && prefersDark) };
}

/** Complete local palette: no old preset or organization colour leaks into the console. */
export function materialTokens(palette: MaterialTheme, dark: boolean): CSSProperties {
  const [bg, surface, low, high, ink, muted, line, primary, onPrimary, container, onContainer] = MATERIAL_THEMES[palette][dark ? "dark" : "light"];
  return {
    colorScheme: dark ? "dark" : "light",
    "--bg": bg, "--surface": surface, "--ink": ink, "--muted": muted,
    "--line": line, "--line-soft": low, "--input-line": line, "--head-bg": low, "--row-hover": low,
    "--brand": primary, "--on-brand": onPrimary, "--brand-dark": primary, "--link": primary, "--dark-link": primary,
    "--side": bg, "--side-ink": ink, "--side-on": container, "--accent": primary, "--tint": container,
    "--material-low": low, "--material-high": high, "--material-container": container, "--material-on-container": onContainer,
    "--code-bg": low, "--chip-n-bg": high,
    "--note-bg": dark ? "#57420b" : "#fff0c2", "--note-line": dark ? "#a78b41" : "#bba15c",
    "--chip-g-bg": dark ? "#144c43" : "#b6f1e4", "--chip-g": dark ? "#8dd5c7" : "#005449",
    "--chip-a-bg": dark ? "#57420b" : "#ffdf91", "--chip-a": dark ? "#ffdf91" : "#674700",
    "--chip-r-bg": dark ? "#633027" : "#ffdad3", "--chip-r": dark ? "#ffb4a7" : "#8b392f",
    "--error": dark ? "#ffb4a7" : "#8b392f", "--outage-bg": dark ? "#633027" : "#ffdad3", "--outage-line": line,
    "--shade": dark ? "#00000099" : "#17252266", "--shadow": "#00000020", "--veil": surface,
  } as CSSProperties;
}
