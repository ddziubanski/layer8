// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderAt } from "../test/util";
import { MerakiDiagnostics } from "./MerakiDiagnostics";
import type { MerakiObservation } from "../api/client";

const OBS: MerakiObservation = {
  network_id: "N_123", network_name: "Example office", mac: "00:11:22:33:44:55", client_id: "c1",
  address: "10.20.1.42", description: "WS-0142", status: "Online", vlan: "20", switch_name: "SW-01",
  switch_serial: "QXXX-EXAM-PLE1", port: "12", connection: "Wired", ssid: "", stale: false,
  last_seen_at: "2026-10-23T07:00:00Z", observed_at: "2026-10-23T07:05:00Z", diagnostics_available: true,
};
const PATH = "/api/meraki/clients/N_123/00%3A11%3A22%3A33%3A44%3A55/diagnostics";
function result(over = {}) {
  const now = new Date().toISOString();
  return { network_id: OBS.network_id, mac: OBS.mac, client_id: OBS.client_id, client_observed_at: OBS.observed_at,
    attempted_at: now, retry_at: new Date(Date.now() + 60000).toISOString(), cached: false, error: null,
    data: { kind: "wired", read_at: now, window_start: now, window_end: now, switch_serial: OBS.switch_serial,
      port: { port_id: "12", status: "Connected", speed: "1 Gbps", duplex: "full", client_count: 2, enabled: true,
        is_uplink: false, errors: [], warnings: ["High proportion of Ethernet collisions"] }, events: [] }, ...over };
}
afterEach(() => vi.unstubAllGlobals());

async function open() {
  renderAt("/", [{ path: "/", element: <MerakiDiagnostics observation={OBS} /> }]);
  await userEvent.click(screen.getByRole("button", { name: "Network diagnostics" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Read diagnostics" })).toBeEnabled());
}

test("opening reads only cache; a provider read requires a second explicit click", async () => {
  const calls = mockApi({ [`GET ${PATH}`]: null, [`POST ${PATH}`]: () => result() });
  renderAt("/", [{ path: "/", element: <MerakiDiagnostics observation={OBS} /> }]);
  expect(calls).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Network diagnostics" }));
  expect(await screen.findByText("No saved diagnostics. Read when you need a check.")).toBeInTheDocument();
  expect(calls.map(c => c.method)).toEqual(["GET"]);
  await userEvent.click(screen.getByRole("button", { name: "Read diagnostics" }));
  expect(await screen.findByText("Connected", { exact: true })).toBeInTheDocument();
  expect(screen.getByText("1 Gbps / full")).toBeInTheDocument();
  expect(screen.getByText("High proportion of Ethernet collisions")).toBeInTheDocument();
  expect(screen.getByText(/port may be shared/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Read diagnostics" })).toBeDisabled();
  expect(screen.getByText(/Available again in/)).toBeInTheDocument();
  expect(calls.map(c => c.method)).toEqual(["GET", "POST"]);
});

test("provider error remains visible alongside the last successful read", async () => {
  const old = result({ retry_at: new Date(Date.now() - 1000).toISOString(), cached: true });
  mockApi({ [`GET ${PATH}`]: old, [`POST ${PATH}`]: () => ({...result(), error: "Meraki refused access", data: old.data}) });
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Read diagnostics" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Meraki refused access The last successful result is shown below.");
  expect(screen.getByText("Connected", { exact: true })).toBeInTheDocument();
});

test("wireless events retain source times and show bounded results", async () => {
  const now = new Date().toISOString();
  mockApi({ [`GET ${PATH}`]: null, [`POST ${PATH}`]: result({data:{kind:"wireless",read_at:now,window_start:now,window_end:now,events_truncated:true,events:[
    {occurred_at:now,type:"dhcp",subtype:"timeout",severity:"bad",device_serial:"QXXX-EXAM-PLE1",rssi:80,channel:36,band:"5",duration_ms:4000},
  ]}}) });
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Read diagnostics" }));
  expect(await screen.findByText("dhcp · timeout")).toBeInTheDocument();
  expect(screen.getByText(/RSSI 80/)).toBeInTheDocument();
  expect(screen.getByText(/Additional events were not fetched/)).toBeInTheDocument();
});

test("empty wireless results make no health claim", async () => {
  const now = new Date().toISOString();
  mockApi({ [`GET ${PATH}`]: null, [`POST ${PATH}`]: result({data:{kind:"wireless",read_at:now,window_start:now,window_end:now,events:[]}}) });
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Read diagnostics" }));
  expect(await screen.findByText(/does not establish that connectivity is healthy/)).toBeInTheDocument();
});

test("request limit errors do not remove earlier cached evidence", async () => {
  const old = result({ retry_at: new Date(Date.now() - 1000).toISOString() });
  mockApi({ [`GET ${PATH}`]: old, [`POST ${PATH}`]: new Response(JSON.stringify({detail:"Meraki diagnostic requests are limited; try again shortly"}),{status:429}) });
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Read diagnostics" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("requests are limited");
  expect(screen.getByText("Connected", {exact:true})).toBeInTheDocument();
});

test("disabled diagnostics expose no action or automatic query", () => {
  const calls = mockApi({});
  renderAt("/", [{ path: "/", element: <MerakiDiagnostics observation={{...OBS,diagnostics_available:false}} /> }]);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(calls).toHaveLength(0);
});
