// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Login } from "../pages/Login";
import { mockApi, renderAt } from "../test/util";
import { TwoFactorSection } from "./TwoFactor";

afterEach(() => vi.unstubAllGlobals());

test("sign-in asks for the code after the password, then sends it", async () => {
  let tries = 0;
  const calls = mockApi({
    "GET /api/auth/options": { windows_sign_in: false, windows_sign_in_silent: false, entra_sign_in: true },
    "GET /api/branding": {},
    "GET /api/about": { product: "Layer 8", version: "1.2.0", license: "AGPL-3.0-or-later", source_download: null, source_repository: "https://github.com/example/layer8" },
    "POST /api/auth/login": () => (++tries === 1 ? new Response(JSON.stringify({ detail: "Enter the code from your authenticator app" }), { status: 401 }) : { id: 1, role: "agent" }),
  });
  renderAt("/", [{ path: "/", element: <Login /> }]);
  expect(await screen.findByRole("link", { name: "Sign in with Microsoft" })).toHaveAttribute("href", "/api/auth/entra/start");
  await userEvent.type(screen.getByLabelText("Email or username"), "sam@example.com");
  await userEvent.type(screen.getByLabelText("Password"), "Correct-horse-battery-9");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await userEvent.type(await screen.findByLabelText(/Code from your authenticator app/), "123456");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(calls.filter((c) => c.path === "/api/auth/login").at(-1)?.body).toEqual({ email: "sam@example.com", password: "Correct-horse-battery-9", code: "123456" }));
});

test("turning it on: confirm a code, then see the recovery codes once", async () => {
  mockApi({
    "GET /api/auth/two-factor": { enabled: false, recovery_codes_left: 0, required: false },
    "POST /api/auth/two-factor/setup": { secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/Layer%208:sam?secret=JBSWY3DPEHPK3PXP&issuer=Layer%208" },
    "POST /api/auth/two-factor/confirm": { recovery_codes: ["aaaaaa-bbbbbb", "cccccc-dddddd"] },
  });
  renderAt("/", [{ path: "/", element: <TwoFactorSection /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Turn on" }));
  await userEvent.click(screen.getByRole("button", { name: "Set up two-factor sign-in" }));
  expect(await screen.findByText("JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Code from the app"), "123456");
  await userEvent.click(screen.getByRole("button", { name: "Turn it on" }));
  expect(await screen.findByText("aaaaaa-bbbbbb")).toBeInTheDocument();
});
