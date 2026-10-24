// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NutanixVmList, TicketVmsCard } from "../components/Nutanix";
import { mockApi, renderAt } from "../test/util";

afterEach(() => vi.unstubAllGlobals());

const NOW = new Date().toISOString();
const VM = {
  ext_id: "vm-1", name: "APP01", power_state: "on", cluster_name: "HQ-cluster", host_name: "ahv-node-1", vcpus: 4, memory_mb: 8192,
  ips: ["10.0.9.9"], macs: ["50:6b:8d:00:00:01"], guest_tools: true, guest_tools_reachable: true, device_id: 7, device_name: "app01",
  present: true, stale: false, observed_at: NOW,
};

function ticketVms(over: Record<string, unknown> = {}) {
  return { vms: [{ ...VM, linked: true }], actions: [], power_enabled: true, may_power: true, may_power_cycle: false, ...over };
}

test("Devices > Virtual machines lists VMs with their device and filters by state", async () => {
  const calls = mockApi({ "GET /api/nutanix/vms": [VM, { ...VM, ext_id: "vm-2", name: "old-test", power_state: "off", device_id: null, device_name: null }] });
  renderAt("/", [{ path: "/", element: <NutanixVmList /> }]);
  expect(await screen.findByText("APP01")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "app01" })).toHaveAttribute("href", "/devices/7");
  expect(screen.getByText("Off")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Power state"), "off");
  await waitFor(() => expect(calls.some((c) => c.path.includes("state=off"))).toBe(true));
});

test("a ticket's VM: restart asks for confirmation, then posts the action", async () => {
  const calls = mockApi({
    "GET /api/nutanix/tickets/4821/vms": ticketVms(),
    "POST /api/nutanix/tickets/4821/vms/vm-1/power": { id: 1, vm_id: "vm-1", vm_name: "APP01", action: "restart", status: "submitted", detail: "", requested_by: "Dev Admin", requested_at: NOW, finished_at: null },
  });
  renderAt("/", [{ path: "/", element: <TicketVmsCard ticketId={4821} onChanged={() => undefined} /> }]);
  expect(await screen.findByText("APP01")).toBeInTheDocument();
  expect(screen.getByText(/Guest Tools answering/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Power cycle" })).not.toBeInTheDocument();  // no permission
  await userEvent.click(screen.getByRole("button", { name: "Restart" }));
  expect(calls.some((c) => c.method === "POST")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Yes, restart" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ action: "restart" }));
});

test("power buttons follow the switch, the state and actions under way", async () => {
  mockApi({ "GET /api/nutanix/tickets/4821/vms": ticketVms({
    vms: [{ ...VM, linked: false, power_state: "off" }],
    actions: [{ id: 2, vm_id: "vm-1", vm_name: "APP01", action: "power_on", status: "running", detail: "", requested_by: "Dev Admin", requested_at: NOW, finished_at: null }],
  }) });
  renderAt("/", [{ path: "/", element: <TicketVmsCard ticketId={4821} onChanged={() => undefined} /> }]);
  expect(await screen.findByText("Power action under way…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Power on" })).not.toBeInTheDocument();
  expect(screen.getByText(/Through device/)).toBeInTheDocument();
  expect(screen.getByText(/running/)).toBeInTheDocument();
});

test("link a VM found by search", async () => {
  const calls = mockApi({
    "GET /api/nutanix/tickets/4821/vms": ticketVms({ vms: [] }),
    "GET /api/nutanix/vms": [{ ...VM, ext_id: "vm-3", name: "files01" }],
    "POST /api/nutanix/tickets/4821/vms": new Response(null, { status: 204 }),
  });
  renderAt("/", [{ path: "/", element: <TicketVmsCard ticketId={4821} onChanged={() => undefined} /> }]);
  await userEvent.type(await screen.findByLabelText("Link a VM"), "files");
  await userEvent.click(await screen.findByRole("button", { name: "Link" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ ext_id: "vm-3" }));
});
