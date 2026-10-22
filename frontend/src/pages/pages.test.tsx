import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, ticket } from "../test/util";
import { QueuePage } from "./QueuePage";
import { TicketPage } from "./TicketPage";

const COUNTS = { open: 12, unassigned: 7, mine: 12, my_queues: 4, sla_at_risk: 3, waiting_on_user: 9, from_email: 18, from_portal: 11 };

afterEach(() => vi.unstubAllGlobals());

describe("queue", () => {
  test("shows the view counts and rows, and switches views", async () => {
    const calls = mockApi({
      "GET /api/tickets/views": COUNTS,
      "GET /api/tickets": ({ path }: { path: string }) => ({
        items: path.includes("view=sla_at_risk") ? [] : [ticket()],
        total: 1,
        limit: 50,
        offset: 0,
      }),
    });
    renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }]);

    const row = await screen.findByRole("row", { name: /Switch port flapping/ });
    expect(within(row).getByText("#4821")).toBeInTheDocument();
    expect(within(row).getByText("2 attachments · Rule: subject contains \"switch\"")).toBeInTheDocument();
    expect(within(row).getByText("Unassigned")).toBeInTheDocument();
    expect(within(row).getByText("0h 42m")).toHaveClass("chip", "a");
    expect(await screen.findByRole("button", { name: "All open 12" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "SLA at risk 3" }));
    expect(await screen.findByText("No tickets in this view.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("view=sla_at_risk"))).toBe(true);
  });
});

describe("queue sorting", () => {
  test("click a column to sort by it, again to reverse; the age column shows how long it's been open", async () => {
    const calls = mockApi({
      "GET /api/tickets/views": COUNTS,
      "GET /api/tickets": { items: [ticket()], total: 1, limit: 50, offset: 0 },
    });
    renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }]);
    const row = await screen.findByRole("row", { name: /Switch port flapping/ });
    expect(within(row).getByText("1h 00m")).toBeInTheDocument(); // opened an hour ago
    expect(calls.at(-1)!.path).toContain("sort=due");

    await userEvent.click(screen.getByRole("button", { name: "Requester" }));
    await waitFor(() => expect(calls.at(-1)!.path).toMatch(/sort=requester&dir=asc/));
    expect(screen.getByRole("columnheader", { name: /Requester/ })).toHaveAttribute("aria-sort", "ascending");

    await userEvent.click(screen.getByRole("button", { name: /Requester/ }));
    await waitFor(() => expect(calls.at(-1)!.path).toMatch(/sort=requester&dir=desc/));
    expect(screen.getByRole("columnheader", { name: /Requester/ })).toHaveAttribute("aria-sort", "descending");

    // Age: longest open first on the first click.
    await userEvent.click(screen.getByRole("button", { name: "Age" }));
    await waitFor(() => expect(calls.at(-1)!.path).toMatch(/sort=age&dir=desc/));
  });
});

describe("ticket", () => {
  function setup(over = {}) {
    const t = ticket(over);
    const calls = mockApi({
      [`GET /api/tickets/${t.id}`]: t,
      [`GET /api/tickets/${t.id}/comments`]: [
        { id: 10, author: { id: 1, email: ME.email, display_name: "J. Park" }, body: "Checked the switch logs", is_internal: true, source: "agent", created_at: t.created_at },
      ],
      [`GET /api/tickets/${t.id}/attachments`]: [
        { id: "a1", filename: "ping-log.txt", content_type: "text/plain", size_bytes: 8192, comment_id: null, uploaded_by_id: 7, uploaded_by: { id: 7, email: "ralvarez@corp.local", display_name: "R. Alvarez" }, created_at: t.created_at, preview: "text" },
        { id: "a2", filename: "switch.cfg.html", content_type: "text/html", size_bytes: 900, comment_id: 10, uploaded_by_id: 1, uploaded_by: { id: 1, email: "jpark@corp.local", display_name: "J. Park" }, created_at: t.created_at, preview: null },
      ],
      [`GET /api/tickets/${t.id}/events`]: [
        { id: 1, type: "routed", data: { rule_name: "switch", queue_name: "Network" }, actor: null, created_at: t.created_at },
      ],
      "GET /api/queues": [
        { id: 1, name: "Service desk", is_active: true },
        { id: 2, name: "Network", is_active: true },
      ],
      "GET /api/queues/2/assignable": [{ id: 5, email: "dcole@corp.local", display_name: "D. Cole" }],
      "GET /api/users": [],
      [`POST /api/tickets/${t.id}/comments`]: { id: 11 },
      [`POST /api/tickets/${t.id}/attachments`]: { id: "a2" },
      [`POST /api/tickets/${t.id}/assign`]: t,
    });
    renderAt(`/tickets/${t.id}`, [{ path: "/tickets/:ticketId", element: <TicketPage me={ME} /> }]);
    return { t, calls };
  }

  test("shows conversation, files, notes, routing and SLA", async () => {
    setup();
    expect(await screen.findByRole("heading", { name: "Switch port flapping, 2nd floor" })).toBeInTheDocument();
    const breadcrumb = screen.getByRole("navigation", { name: "Ticket breadcrumb" });
    expect(within(breadcrumb).getByRole("link", { name: "Tickets" })).toHaveAttribute("href", "/");
    expect(within(breadcrumb).getByText("#4821")).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("SERVICE DESK / TICKET DETAILS")).toBeInTheDocument();
    expect(screen.getByText("Requested by")).toHaveTextContent("R. Alvarez");
    expect(screen.getByText("Switch port flapping, 2nd floor").closest(".ticket-workspace-head")).toBeInTheDocument();

    expect(screen.getByText(/R\. Alvarez, via email/)).toBeInTheDocument();
    // Files are listed in the Attachments section; messages just mention them.
    expect(screen.getByText("Attached: ping-log.txt")).toBeInTheDocument();
    expect(screen.getByText("Attachments (2)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download ping-log.txt" })).toHaveAttribute("href", "/api/attachments/a1/download");
    expect(screen.getByText(/8 KB · R\. Alvarez · .* · original request/)).toBeInTheDocument();
    expect(screen.getByText(/900 B · you · .* · internal note/)).toBeInTheDocument();
    // HTML is download-only: no View button.
    expect(screen.queryByRole("button", { name: "View switch.cfg.html" })).toBeNull();
    expect(screen.getByText("Checked the switch logs").closest(".card")).toHaveClass("note", "ticket-message", "ticket-message-internal");
    expect(screen.getByText(/R\. Alvarez, via email/).closest(".card")).toHaveClass("ticket-message-original");
    expect(screen.getByText('Routed by rule: subject contains "switch"')).toBeInTheDocument();
    expect(screen.getByText("SLA breach in 0h 42m")).toBeInTheDocument();
    expect(await screen.findByText('Routed to Network by rule "switch"')).toBeInTheDocument();
  });

  test("previews a file in place, with download alongside", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "View ping-log.txt" }));
    const dialog = screen.getByRole("dialog", { name: "Preview of ping-log.txt" });
    expect(within(dialog).getByTitle("ping-log.txt")).toHaveAttribute("src", "/api/attachments/a1/view");
    expect(within(dialog).getByRole("link", { name: "Download" })).toHaveAttribute("href", "/api/attachments/a1/download");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("moving to another queue asks for an optional reason", async () => {
    const { calls } = setup();
    await screen.findByRole("option", { name: "Service desk" });
    await userEvent.selectOptions(screen.getByLabelText("Queue"), "Service desk");
    await userEvent.type(screen.getByLabelText(/Reason for moving/), "Not a network issue");
    await userEvent.click(screen.getByRole("button", { name: "Move ticket" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/assign"))?.body).toEqual({
        queue_id: 1,
        unassign: false,
        note: "Not a network issue",
      }),
    );
  });

  test("sends a reply to the requester, then uploads the attached file onto that comment", async () => {
    const { t, calls } = setup();
    const box = await screen.findByLabelText("Work notes and replies");
    await userEvent.type(box, "Can you try port 15?");
    await userEvent.upload(screen.getByTestId("file-input"), new File(["x"], "diagram.png", { type: "image/png" }));
    expect(screen.getByText(/diagram\.png/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Show to the requester in the portal"));
    expect(screen.getByLabelText("Email it to them too")).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Send to requester" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/attachments"))).toBe(true));
    const comment = calls.find((c) => c.method === "POST" && c.path === `/api/tickets/${t.id}/comments`)!;
    expect(comment.body).toEqual({ body: "Can you try port 15?", is_internal: false, email_requester: true });
    const upload = calls.find((c) => c.path.endsWith("/attachments") && c.method === "POST")!;
    expect((upload.body as FormData).get("comment_id")).toBe("11");
    await waitFor(() => expect(box).toHaveValue(""));
  });

  test("notes are internal unless shown to the requester; showing without emailing posts to the portal only", async () => {
    const { calls } = setup();
    const box = await screen.findByLabelText("Work notes and replies");
    expect(screen.getByLabelText("Email it to them too")).toBeDisabled();
    await userEvent.type(box, "note");
    await userEvent.click(screen.getByRole("button", { name: "Add internal note" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ body: "note", is_internal: true, email_requester: false }));

    await waitFor(() => expect(box).toHaveValue(""));
    await userEvent.type(box, "Fixed it remotely");
    await userEvent.click(screen.getByLabelText("Show to the requester in the portal"));
    await userEvent.click(screen.getByLabelText("Email it to them too"));
    await userEvent.click(screen.getByRole("button", { name: "Post to portal" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "POST" && c.path.endsWith("/comments")).at(-1)?.body).toEqual({ body: "Fixed it remotely", is_internal: false, email_requester: false }),
    );
  });

  test("resting the mouse on a file previews it; dropping files on Attachments adds them on an internal note", async () => {
    const { t, calls } = setup();
    const name = await screen.findByRole("button", { name: "ping-log.txt" });
    await userEvent.hover(name);
    const tip = await screen.findByRole("tooltip", { name: "Preview of ping-log.txt" });
    expect(within(tip).getByTitle("Preview of ping-log.txt")).toHaveAttribute("src", "/api/attachments/a1/view");
    await userEvent.unhover(name);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());

    const section = screen.getByText(/^Attachments \(/).closest(".card")!;
    const file = new File(["cfg"], "running.cfg", { type: "text/plain" });
    fireEvent.drop(section, { dataTransfer: { types: ["Files"], files: [file] } });
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/api/tickets/${t.id}/attachments`)).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/comments"))?.body).toEqual({ body: "Added files: running.cfg", is_internal: true, email_requester: false });
  });

  test("files can be dragged onto the composer", async () => {
    setup();
    const box = await screen.findByLabelText("Work notes and replies");
    const card = box.closest(".composer")!;
    const file = new File(["log"], "ping.txt", { type: "text/plain" });
    const dataTransfer = { types: ["Files"], files: [file], dropEffect: "" };
    fireEvent.dragEnter(card, { dataTransfer });
    expect(screen.getByText("Drop files to attach them")).toBeInTheDocument();
    fireEvent.drop(card, { dataTransfer });
    expect(screen.getByRole("list", { name: "Files to attach" })).toHaveTextContent("ping.txt");
  });

  test("assignee list comes from the queue's owners", async () => {
    const { calls } = setup();
    const select = await screen.findByLabelText("Assignee");
    await screen.findByRole("option", { name: "D. Cole" });
    await userEvent.selectOptions(select, "D. Cole");
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/assign"))?.body).toEqual({ assignee_id: 5, unassign: false }),
    );
  });

  test("escalate asks for a reason and is disabled on resolved tickets", async () => {
    setup({ status: "resolved" });
    expect(await screen.findByRole("button", { name: "Escalate" })).toBeDisabled();
    expect(screen.getByText("Reopen the ticket to escalate it.")).toBeInTheDocument();
  });
});

describe("queue scope, colours and bulk moves", () => {
  test("my tickets / my groups / all; the choice sticks", async () => {
    const calls = mockApi({
      "GET /api/tickets/views": COUNTS,
      "GET /api/tickets": { items: [ticket()], total: 1, limit: 100, offset: 0 },
    });
    renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }]);
    const scope = await screen.findByRole("group", { name: "Whose tickets" });
    expect(within(scope).getByRole("button", { name: "All tickets" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(scope).getByRole("button", { name: "My groups" }));
    await waitFor(() => expect(calls.some((c) => c.path.includes("scope=groups") && c.path.startsWith("/api/tickets?"))).toBe(true));
    expect(calls.some((c) => c.path === "/api/tickets/views?scope=groups")).toBe(true);
    expect(localStorage.getItem("helpdesk.queue.scope")).toBe("groups");
    localStorage.removeItem("helpdesk.queue.scope");
  });

  test("the assignee's colour as a stripe, my rule as a tint", async () => {
    const t = ticket({ priority: "critical", assignee: { id: 2, email: "dcole@corp.local", display_name: "D. Cole", ticket_color: "#2563eb" } });
    mockApi({ "GET /api/tickets/views": COUNTS, "GET /api/tickets": { items: [t], total: 1, limit: 100, offset: 0 } });
    const me = { ...ME, color_rules: [{ field: "priority" as const, value: "critical", color: "#dc2626" }] };
    renderAt("/", [{ path: "/", element: <QueuePage me={me} /> }]);
    const row = await screen.findByRole("row", { name: /Switch port flapping/ });
    expect(row).toHaveClass("tinted");
    expect(row.style.getPropertyValue("--stripe")).toBe("#2563eb");
    expect(row.style.getPropertyValue("--rule")).toBe("#dc2626");
  });

  test("select tickets and move them to another queue", async () => {
    let listed = [ticket(), ticket({ id: 4822, subject: "VPN down" })];
    const calls = mockApi({
      "GET /api/tickets/views": COUNTS,
      "GET /api/tickets": () => ({ items: listed, total: listed.length, limit: 100, offset: 0 }),
      "GET /api/queues": [{ id: 2, name: "Network", is_active: true }],
      "GET /api/users": [],
      "POST /api/tickets/bulk/assign": () => ((listed = []), { done: [4821, 4822], failed: {} }),
    });
    renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }]);
    await screen.findByRole("row", { name: /VPN down/ });
    await userEvent.click(screen.getByLabelText("Select all on this page"));
    const bar = screen.getByRole("region", { name: "Selected tickets" });
    expect(within(bar).getByText("2 selected")).toBeInTheDocument();
    await userEvent.selectOptions(within(bar).getByLabelText("Move to queue"), "2");
    await userEvent.type(within(bar).getByLabelText("Reason (optional)"), "Network outage");
    await userEvent.click(within(bar).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Selected tickets" })).toBeNull());
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ ticket_ids: [4821, 4822], queue_id: 2, assignee_id: null, unassign: false, note: "Network outage" });
  });
});

describe("queue switcher", () => {
  test("pick a queue in the corner: the list and counts follow, and it's remembered", async () => {
    localStorage.removeItem("helpdesk.queue.queue");
    const calls = mockApi({
      "GET /api/queues": [{ id: 3, name: "HR" }, { id: 4, name: "Maintenance" }],
      "GET /api/tickets/views": COUNTS,
      "GET /api/tickets": { items: [ticket()], total: 1, limit: 50, offset: 0 },
    });
    renderAt("/", [{ path: "/", element: <QueuePage me={ME} /> }]);
    const pick = await screen.findByRole("combobox", { name: "Queue" });
    expect(pick).toHaveValue("");
    await userEvent.selectOptions(pick, await screen.findByRole("option", { name: "Maintenance" }));
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/api/tickets?") && c.path.includes("queue_id=4"))).toBe(true));
    expect(calls.some((c) => c.path.startsWith("/api/tickets/views") && c.path.includes("queue_id=4"))).toBe(true);
    expect(localStorage.getItem("helpdesk.queue.queue")).toBe("4");
    // Back to all queues.
    await userEvent.selectOptions(pick, "");
    expect(localStorage.getItem("helpdesk.queue.queue")).toBe("");
  });
});
