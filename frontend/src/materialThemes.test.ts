// SPDX-License-Identifier: AGPL-3.0-or-later
import { contrast } from "./branding";
import { consoleAppearance, MATERIAL_THEMES, materialTokens, type MaterialTheme } from "./materialThemes";
import { ME } from "./test/util";
import type { Branding } from "./api/client";

test("all paired palettes preserve readable text and button contrast", () => {
  for (const name of Object.keys(MATERIAL_THEMES) as MaterialTheme[]) for (const dark of [false, true]) {
    const tokens = materialTokens(name, dark) as Record<string, string>;
    for (const [fg, bg] of [["--ink", "--surface"], ["--muted", "--surface"], ["--muted", "--material-low"], ["--on-brand", "--brand"], ["--material-on-container", "--material-container"], ["--chip-g", "--chip-g-bg"], ["--chip-a", "--chip-a-bg"], ["--chip-r", "--chip-r-bg"]]) {
      expect(contrast(tokens[fg], tokens[bg]), `${name}/${dark}/${fg}`).toBeGreaterThanOrEqual(4.5);
    }
  }
});
test("Tidepool fallback and personal/organization/device preferences compose", () => {
  expect(consoleAppearance(ME, {} as Branding, false)).toEqual({ palette: "tidepool", dark: false });
  const brand = { console_theme: "blue-steel", dark_mode: "auto" } as Branding;
  expect(consoleAppearance(ME, brand, true)).toEqual({ palette: "blue-steel", dark: true });
  expect(consoleAppearance({ ...ME, console_appearance: { palette: "terracotta", mode: "light" } }, brand, true)).toEqual({ palette: "terracotta", dark: false });
});
