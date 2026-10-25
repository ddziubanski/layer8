// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { backgroundStyle } from "../branding";
import { BrandingAdmin } from "../pages/admin/BrandingAdmin";
import { Login } from "../pages/Login";
import { ME, mockApi, renderAt } from "./util";
import type { Branding } from "../api/client";

afterEach(() => vi.unstubAllGlobals());
const brand={product_name:"Example IT",sign_in_heading:"Welcome to Example IT",background_url:"/api/branding/files/background?v=1",sign_in_layout:"right",sign_in_card_size:"wide",sign_in_background_fit:"contain",sign_in_background_position:"left",sign_in_background_overlay:40,sign_in_backdrop:"ocean"} as Branding;

test("appearance changes are confined to sign-in and preserve portal background behavior",() => {
  const login=backgroundStyle(brand,"sign-in")!;
  expect(login.backgroundImage).toContain("0.4");
  expect(login.backgroundImage).toContain(brand.background_url);
  expect(login.backgroundSize).toContain("contain");
  expect(login.backgroundPosition).toContain("left center");
  expect((login as Record<string,unknown>)["--login-align"]).toBe("end");
  const portal=backgroundStyle(brand,"portal")!;
  expect(portal.backgroundImage).toBe(`url("${brand.background_url}")`);
  expect(portal.backgroundSize).toBe("cover");
  expect(backgroundStyle(brand,"console")).toBeUndefined();
});

test("custom welcome text remains plain text beside working authentication controls",async () => {
  mockApi({"GET /api/branding":{...brand,sign_in_heading:"<img src=x onerror=alert(1)>"},"GET /api/auth/options":{windows_sign_in:false,windows_sign_in_silent:false},"GET /api/about":{product:"Layer 8",version:"1.5.1",license:"AGPL-3.0-or-later",source_repository:"https://github.com/ddziubanski/layer8",source_download:"/api/source"}});
  const {container}=renderAt("/",[{path:"/",element:<Login />}]);
  expect(await screen.findByRole("heading",{name:"<img src=x onerror=alert(1)>"})).toBeInTheDocument();
  expect(container.querySelector('img[src="x"]')).toBeNull();
  expect(screen.getByLabelText("Email or username")).toBeInTheDocument();
  expect(screen.getByLabelText("Password")).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Sign in"})).toBeInTheDocument();
  expect(await screen.findByRole("link",{name:"Source code"})).toBeInTheDocument();
});

test("admin previews unsaved placement and overlay and saves only appearance changes",async () => {
  const fields=[
    {name:"sign_in_layout",label:"Card placement",kind:"choice",value:"center",choices:[{value:"center",label:"Center"},{value:"right",label:"Right"}]},
    {name:"sign_in_background_overlay",label:"Image darkening (%)",kind:"int",value:0},
  ];
  const calls=mockApi({"GET /api/branding":brand,"GET /api/settings/branding":{fields:[]},"GET /api/settings/theme":{fields:[]},
    "GET /api/settings/signin_appearance":{section:"signin_appearance",fields},
    "PUT /api/settings/signin_appearance":{section:"signin_appearance",fields}});
  renderAt("/",[{path:"/",element:<BrandingAdmin me={{...ME,role:"admin"}} />}]);
  const form=await screen.findByRole("form",{name:"Sign-in appearance"});
  const preview=within(form).getByRole("img",{name:"Sign-in appearance preview"});
  expect(preview.style.getPropertyValue("--login-align")).toBe("center");
  await userEvent.selectOptions(within(form).getByLabelText("Card placement"),"right");
  await userEvent.clear(within(form).getByLabelText("Image darkening (%)"));
  await userEvent.type(within(form).getByLabelText("Image darkening (%)"),"50");
  expect(preview.style.getPropertyValue("--login-align")).toBe("end");
  expect(preview.style.backgroundImage).toContain("0.5");
  expect(calls.every(call=>call.method==="GET")).toBe(true);
  await userEvent.click(within(form).getByRole("button",{name:"Save"}));
  await waitFor(()=>expect(calls.find(call=>call.method==="PUT")?.body).toEqual({values:{sign_in_layout:"right",sign_in_background_overlay:"50"},clear:[]}));
});
