import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt } from "../test/util";
import { Home } from "./Home";
import { MyTickets } from "./MyTickets";
import { NewRequest } from "./NewRequest";
import { PortalTicket } from "./PortalTicket";

const REQUESTER = { ...ME, id: 7, email: "ralvarez@corp.local", display_name: "R. Alvarez", role: "requester" as const };
const QUEUES = [
  { id: 2, name: "Network", description: "Wi-Fi, wired, VPN", categories: [] },
  { id: 3, name: "Printers & scanners", description: null, categories: ["Jam", "Toner"] },
];
const PT = {
  id: 1005,
  label: "#1005",
  subject: "Toner low",
  description: "Front desk",
  category: "Toner",
  status: "open",
  queue_name: "Printers & scanners",
  assignee_name: "J. Park",
  attachment_count: 0,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  resolved_at: null,
  closed_at: null,
};

const routes = [
  { path: "/", element: <Home /> },
  { path: "/new", element: <NewRequest /> },
  { path: "/tickets", element: <MyTickets /> },
  { path: "/tickets/:ticketId", element: <PortalTicket me={REQUESTER} /> },
];

afterEach(() => vi.unstubAllGlobals());

test("request conversation failures remain visible without exposing reply controls on closed requests", async () => {
  mockApi({
    "GET /api/portal/tickets/1005": { ...PT, status: "closed" },
    "GET /api/tickets/1005/comments": new Response('{"detail":"temporarily unavailable"}', { status: 503 }),
    "GET /api/tickets/1005/attachments": [],
    "GET /api/portal/tickets/1005/survey": new Response('{}', { status: 404 }),
  });
  renderAt("/tickets/1005", routes);
  const conversation = await screen.findByRole("region", { name: "Conversation" });
  expect(await within(conversation).findByRole("alert")).toHaveTextContent("Replies could not be loaded");
  expect(screen.getByRole("complementary", { name: "Request documents" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Send reply" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Make a new request" })).toHaveAttribute("href", "/");
});

test("guided request: team, then that team's options, then details", async () => {
  const calls = mockApi({
    "GET /api/portal/queues": QUEUES,
    "POST /api/portal/tickets": PT,
    "POST /api/tickets/1005/attachments": { id: "f1" },
  });
  renderAt("/", routes);
  // Only question 1 at first.
  await screen.findByLabelText("Which team can help?");
  expect(screen.queryByLabelText("What is the issue with?")).toBeNull();
  expect(screen.queryByLabelText("Subject")).toBeNull();

  // By value: user-event compares option innerHTML, where "&" is "&amp;".
  await userEvent.selectOptions(screen.getByLabelText("Which team can help?"), "3");
  // The team's options "flip" in as tiles.
  const issue = screen.getByRole("radiogroup", { name: "What is the issue with?" });
  expect(within(issue).getAllByRole("radio").map((r) => r.closest("label")!.textContent)).toEqual(["Jam", "Toner", "Something else…"]);
  expect(screen.queryByLabelText("Subject")).toBeNull();

  await userEvent.click(within(issue).getByLabelText("Toner"));
  await userEvent.click(screen.getByLabelText("Just me"));
  await userEvent.click(screen.getByLabelText("Urgent: I'm blocked"));
  await userEvent.type(screen.getByLabelText("Subject"), "Toner low");
  await userEvent.type(screen.getByLabelText("Details"), "Front desk");
  await userEvent.upload(screen.getByTestId("file-input"), new File(["x"], "photo.jpg", { type: "image/jpeg" }));
  await userEvent.click(screen.getByRole("button", { name: "Send request" }));

  expect(await screen.findByText("Request sent")).toBeInTheDocument();
  expect(screen.getByText("#1005")).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/portal/tickets")?.body).toEqual({ queue_id: 3, category: "Toner", subject: "Toner low", description: "Front desk", impact: "single", urgency: "high", computer_id: null });
  expect(calls.some((c) => c.path === "/api/tickets/1005/attachments")).toBe(true);
});

test("'Something else' takes free text", async () => {
  const calls = mockApi({ "GET /api/portal/queues": QUEUES, "POST /api/portal/tickets": PT });
  renderAt("/new?queue=3", routes);
  await userEvent.click(await screen.findByLabelText("Something else…"));
  expect(screen.queryByLabelText("Subject")).toBeNull();
  await userEvent.type(screen.getByLabelText("Describe what the issue is with"), "Label printer");
  await userEvent.click(screen.getByLabelText("Just me"));
  await userEvent.click(screen.getByLabelText("It can wait"));
  await userEvent.type(screen.getByLabelText("Subject"), "Labels smudge");
  await userEvent.click(screen.getByRole("button", { name: "Send request" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ queue_id: 3, category: "Label printer" }));
});

test("a team without options goes straight to details", async () => {
  mockApi({ "GET /api/portal/queues": QUEUES });
  renderAt("/new?queue=2", routes);
  expect(await screen.findByText("Wi-Fi, wired, VPN")).toBeInTheDocument();
  expect(screen.queryByLabelText("What is the issue with?")).toBeNull();
  expect(screen.getByRole("radiogroup", { name: "Who is affected?" })).toBeInTheDocument();
});

test("my tickets shows open tickets and who's on them", async () => {
  mockApi({ "GET /api/portal/tickets": { items: [PT], total: 1, limit: 200, offset: 0 } });
  renderAt("/tickets", routes);
  expect(await screen.findByRole("link", { name: "#1005 Toner low" })).toHaveAttribute("href", "/tickets/1005");
  expect(screen.getByText(/J\. Park is on it/)).toBeInTheDocument();
});

test("ticket view: reply and 'this is fixed'", async () => {
  const calls = mockApi({
    "GET /api/portal/tickets/1005": PT,
    "GET /api/tickets/1005/comments": [],
    "GET /api/tickets/1005/attachments": [],
    "POST /api/tickets/1005/comments": { id: 50 },
    "POST /api/portal/tickets/1005/resolve": { ...PT, status: "resolved" },
  });
  renderAt("/tickets/1005", routes);
  await userEvent.type(await screen.findByLabelText("Add a reply"), "Thanks!");
  await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ body: "Thanks!", is_internal: false, email_requester: true }));
  await userEvent.click(screen.getByRole("button", { name: "This is fixed" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/portal/tickets/1005/resolve")).toBe(true));
  expect(screen.getByText("Attachments (0)")).toBeInTheDocument();
});

test("agents use the portal under /portal, as themselves", async () => {
  const { portalRoutes } = await import("./PortalApp");
  mockApi({ "GET /api/portal/queues": QUEUES, "GET /api/portal/tickets": { items: [PT], total: 1, limit: 200, offset: 0 } });
  renderAt("/portal/tickets", [portalRoutes(ME, "/portal")]);
  expect(await screen.findByRole("link", { name: "#1005 Toner low" })).toHaveAttribute("href", "/portal/tickets/1005");
  expect(screen.getByRole("link", { name: "Back to console" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "New request" })).toHaveAttribute("href", "/portal");
});

test("a long option list uses a dropdown instead of tiles", async () => {
  const many = Array.from({ length: 15 }, (_, i) => `Option ${i + 1}`);
  mockApi({ "GET /api/portal/queues": [{ id: 5, name: "Maintenance", description: null, categories: many }] });
  renderAt("/new?queue=5", routes);
  const select = await screen.findByLabelText("What is the issue with?");
  expect(select.tagName).toBe("SELECT");
  expect(screen.queryByRole("radiogroup")).toBeNull();
});

test("which computer: offered from the requester's own, one picked for them", async () => {
  const calls = mockApi({
    "GET /api/portal/queues": [],
    "GET /api/portal/devices": [{ id: 7, name: "WS-0142", os: "Windows 11" }, { id: 8, name: "LT-0099", os: null }],
    "POST /api/portal/tickets": { id: 9 },
  });
  renderAt("/", [{ path: "/", element: <NewRequest /> }]);
  await userEvent.click(await screen.findByLabelText("Just me"));
  await userEvent.click(screen.getByLabelText("Urgent: I'm blocked"));
  const pc = await screen.findByLabelText(/Which computer/);
  // Two computers: none assumed until they pick.
  await userEvent.selectOptions(pc, "8");
  await userEvent.type(screen.getByLabelText("Subject"), "Wi-Fi drops");
  await userEvent.click(screen.getByRole("button", { name: "Send request" }));
  await waitFor(() => expect(calls.find((c) => c.path === "/api/portal/tickets")?.body).toMatchObject({ computer_id: 8 }));
});
