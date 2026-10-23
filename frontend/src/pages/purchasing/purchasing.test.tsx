import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { PurchaseOrderPage } from "./PurchaseOrdersPage";
import { ReceivePage, ReceivingSheet } from "./Receiving";

afterEach(() => (vi.unstubAllGlobals(), window.history.pushState({}, "", "/")));

const PO = {
  id: 12, label: "PO-00012", title: "Monitors for Finance", vendor: { id: 1, name: "CDW", support_phone: "", support_email: "", support_portal: "", support_hours: "", account_number: "" },
  department: null, requested_by: null, approver: null, status: "ordered", order_date: "2026-10-19", expected_date: "2026-10-24", currency: "USD", total: 1070.96,
  lines_count: 1, lines_received: 0, created_at: "2026-10-19T00:00:00Z", updated_at: "2026-10-19T00:00:00Z", ship_to: "HQ, loading dock B", budget_code: "",
  vendor_reference: "Q-7781", shipping: 25, terms: "", notes: "", approved_at: null, subtotal: 799.96, tax: 66, receiving_code: "K7M4PX",
  receiving_url: "https://helpdesk.corp.example/receive?po=PO-00012&code=K7M4PX", can_approve: true,
  lines: [{ id: 3, item: "Dell P2425 monitor", description: "", part_number: "210-BMHN", quantity: 4, unit_price: 199.99, tax_rate: 8.25, received_quantity: 0, line_total: 865.96 }],
  tickets: [], devices: [], attachments: [], events: [],
};

test("a new purchase order with a line, totalled as you type", async () => {
  const calls = mockApi({
    "GET /api/vendors": [PO.vendor],
    "GET /api/field-options": [],
    "GET /api/users": [],
    "POST /api/purchase-orders": ({ body }: Call) => ({ ...PO, ...(body as object), id: 13, status: "draft" }),
  });
  renderAt("/purchase-orders/new", [{ path: "/purchase-orders/:poId", element: <PurchaseOrderPage me={ME} /> }]);
  const form = await screen.findByRole("form", { name: "Purchase order" });
  await userEvent.type(within(form).getByLabelText("Title"), "Docks for Sales");
  await userEvent.selectOptions(within(form).getByLabelText("Vendor"), await within(form).findByRole("option", { name: "CDW" }));
  const line = within(form).getByRole("group", { name: "Line 1" });
  await userEvent.type(within(line).getByLabelText("Item"), "USB-C dock");
  await userEvent.clear(within(line).getByLabelText("Quantity"));
  await userEvent.type(within(line).getByLabelText("Quantity"), "3");
  await userEvent.clear(within(line).getByLabelText("Unit price"));
  await userEvent.type(within(line).getByLabelText("Unit price"), "100");
  expect(within(form).getByText(/Total \$300\.00/)).toBeInTheDocument();
  await userEvent.click(within(form).getByRole("button", { name: "Create purchase order" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ title: "Docks for Sales", vendor_id: 1, lines: [{ item: "USB-C dock", quantity: 3, unit_price: 100 }] }),
  );
});

test("order summary preserves approval restrictions and existing status actions", async () => {
  let current = { ...PO, status: "requested", approver: { ...ME }, can_approve: false, vendor: { ...PO.vendor, name: "Example supplier" } };
  const calls = mockApi({
    "GET /api/purchase-orders/12": () => current,
    "GET /api/vendors": [current.vendor], "GET /api/field-options": [], "GET /api/users": [],
    "POST /api/purchase-orders/12/status": ({ body }: Call) => (current = { ...current, ...(body as object) }),
  });
  renderAt("/purchase-orders/12", [{ path: "/purchase-orders/:poId", element: <PurchaseOrderPage me={ME} /> }]);
  const summary = await screen.findByLabelText("Order summary");
  expect(summary).toHaveTextContent("Example supplier");
  expect(summary).toHaveTextContent("Lines fully received0 / 1");
  const actions = screen.getByRole("group", { name: "Order actions" });
  expect(within(actions).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  expect(within(actions).queryByRole("button", { name: "Mark ordered" })).not.toBeInTheDocument();
  await userEvent.click(within(actions).getByRole("button", { name: "Cancel PO" }));
  await waitFor(() => expect(calls.find(c => c.path.endsWith("/status"))?.body).toEqual({ status: "cancelled", note: "" }));
  expect(await within(actions).findByRole("button", { name: "Reopen as draft" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Receiving sheet" })).toHaveAttribute("href", "/purchase-orders/12/receiving");
});

test("receiving lookup still submits the order number and uppercased code", async () => {
  const calls = mockApi({ "GET /api/branding": {}, "POST /api/public/receiving/lookup": { label: "PO-00012", title: "Example delivery", vendor: "Example supplier", status: "Ordered", expected_date: null, lines: [] } });
  renderAt("/receive", [{ path: "/receive", element: <ReceivePage /> }]);
  const lookup = screen.getByRole("form", { name: "Find a delivery" });
  await userEvent.type(within(lookup).getByLabelText("PO number"), "PO-00012");
  await userEvent.type(within(lookup).getByLabelText(/Code/), "k7m4px");
  await userEvent.click(within(lookup).getByRole("button", { name: "Find it" }));
  expect(await screen.findByRole("heading", { name: /Example delivery/ })).toBeInTheDocument();
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ po: "PO-00012", code: "K7M4PX" });
  expect(screen.getByRole("button", { name: "Mark received" })).toBeDisabled();
});

test("the receiving sheet writes out the address, PO number and code (no scanner needed)", async () => {
  mockApi({ "GET /api/purchase-orders/12": PO, "GET /api/branding": { product_name: "Layer 8" } });
  renderAt("/purchase-orders/12/receiving", [{ path: "/purchase-orders/:poId/receiving", element: <ReceivingSheet /> }]);
  expect(await screen.findByText("https://helpdesk.corp.example/receive")).toBeInTheDocument();
  expect(screen.getAllByText("PO-00012").length).toBeGreaterThan(0);
  expect(screen.getByText("K7M4PX")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "Dell P2425 monitor" })).toBeInTheDocument();
});

test("the public receiving page, opened from the QR code: count, name, done", async () => {
  window.history.pushState({}, "", "/receive?po=PO-00012&code=K7M4PX");
  const view = { label: "PO-00012", title: "Monitors for Finance", vendor: "CDW", expected_date: "2026-10-24", ship_to: "", status: "Ordered", lines: [{ id: 3, item: "Dell P2425 monitor", part_number: "210-BMHN", quantity: 4, received_quantity: 0 }] };
  const calls = mockApi({
    "GET /api/branding": { product_name: "Layer 8" },
    "POST /api/public/receiving/lookup": view,
    "POST /api/public/receiving/receive": { ...view, status: "Received", lines: [{ ...view.lines[0], received_quantity: 4 }] },
  });
  renderAt("/receive", [{ path: "/receive", element: <ReceivePage /> }]);
  const form = await screen.findByRole("form", { name: "What arrived" });
  await userEvent.click(within(form).getByRole("button", { name: "All 4" }));
  await userEvent.type(within(form).getByLabelText("Your name"), "Pat");
  await userEvent.click(within(form).getByRole("button", { name: "Mark received" }));
  expect(await screen.findByText(/everything on this order is in/)).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/public/receiving/receive")!.body).toMatchObject({ po: "PO-00012", code: "K7M4PX", name: "Pat", lines: [{ line_id: 3, quantity: 4 }] });
});

test("arrange the purchase order page: files across the top, history hidden", async () => {
  const USUAL = { top: [], main: ["order"], side: ["receive", "heads_up", "files", "history"], bottom: [], hidden: [] };
  const calls = mockApi({
    "GET /api/purchase-orders/12": PO,
    "GET /api/vendors": [PO.vendor],
    "GET /api/field-options": [],
    "GET /api/users": [],
    "GET /api/layouts/po": { page: "po", effective: USUAL, default: USUAL, mine: null, required: ["order"] },
    "PUT /api/layouts/po/mine": ({ body }: Call) => ({ page: "po", effective: body, default: USUAL, mine: body, required: ["order"] }),
  });
  renderAt("/purchase-orders/12", [{ path: "/purchase-orders/:poId", element: <PurchaseOrderPage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Arrange this page" }));
  // The order itself can't be hidden.
  expect(screen.queryByRole("button", { name: "Hide The order" })).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Where Files goes"), "top");
  await userEvent.click(screen.getByRole("button", { name: "Hide History" }));
  await userEvent.click(within(screen.getByRole("region", { name: "Arrange this page" })).getByRole("button", { name: "Save for me" }));
  await waitFor(() => {
    const sent = calls.find((c) => c.method === "PUT" && c.path === "/api/layouts/po/mine")!.body as typeof USUAL;
    expect(sent.top).toEqual(["files"]);
    expect(sent.hidden).toEqual(["history"]);
  });
  await waitFor(() => expect(screen.queryByRole("region", { name: "History" })).not.toBeInTheDocument());
  expect(screen.getByRole("region", { name: "Files" })).toBeInTheDocument();
});

test("arrange the order form's fields: hide one, move one, see it as you go", async () => {
  const USUAL = { top: [], main: ["order"], side: ["receive", "heads_up", "files", "history"], bottom: [], hidden: [], fields: {} };
  const calls = mockApi({
    "GET /api/purchase-orders/12": PO,
    "GET /api/vendors": [PO.vendor],
    "GET /api/field-options": [],
    "GET /api/users": [],
    "GET /api/layouts/po": { page: "po", effective: USUAL, default: USUAL, mine: null, required: ["order"] },
    "PUT /api/layouts/po/mine": ({ body }: Call) => ({ page: "po", effective: body, default: USUAL, mine: body, required: ["order"] }),
  });
  renderAt("/purchase-orders/12", [{ path: "/purchase-orders/:poId", element: <PurchaseOrderPage me={ME} /> }]);
  const form = await screen.findByRole("form", { name: "Purchase order" });
  expect(within(form).getByLabelText("Budget / cost code")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Arrange this page" }));
  const top = screen.getByRole("group", { name: "Fields at the top" });
  await userEvent.click(within(within(top).getByRole("group", { name: "Field: Budget / cost code" })).getByLabelText("Shown"));
  expect(within(screen.getByRole("form", { name: "Purchase order" })).queryByLabelText("Budget / cost code")).not.toBeInTheDocument();
  await userEvent.click(within(top).getByRole("button", { name: "Move Vendor quote / order number up" }));
  await userEvent.click(within(within(top).getByRole("group", { name: "Field: Vendor" })).getByLabelText("Full width"));
  await userEvent.click(within(screen.getByRole("region", { name: "Arrange this page" })).getByRole("button", { name: "Save for me" }));
  await waitFor(() => {
    const sent = calls.find((c) => c.method === "PUT")!.body as { fields: Record<string, { order: string[]; hidden: string[]; wide: string[] }> };
    expect(sent.fields.order_top.order.slice(0, 2)).toEqual(["vendor_reference", "vendor"]);
    expect(sent.fields.order_top.hidden).toEqual(["budget_code"]);
    expect(sent.fields.order_top.wide).toEqual(["vendor"]);
  });
});
