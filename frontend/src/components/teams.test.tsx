import { StrictMode } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderAt } from "../test/util";
import { TeamsCallback, TeamsConnection } from "./TeamsConnection";
import { TeamsReply } from "../pages/ticket/TeamsReply";

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });
const connected = { configured: true, connected: true, account: "agent@example.com" };

test("Microsoft connection displays the sender and disconnects only this account", async () => {
  let active = true;
  const calls = mockApi({
    "GET /api/teams/connection": () => active ? connected : { ...connected, connected: false, account: null },
    "DELETE /api/teams/connection": () => { active = false; return null; },
  });
  renderAt("/", [{ path: "/", element: <TeamsConnection /> }]);
  expect(await screen.findByText("agent@example.com")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Disconnect Teams" }));
  expect(await screen.findByRole("button", { name: "Connect Microsoft Teams" })).toBeInTheDocument();
  expect(calls.filter(c => c.method === "DELETE")).toHaveLength(1);
});

test("callback clears the fragment and completes once under StrictMode", async () => {
  window.history.replaceState(null, "", "/teams/callback#code=synthetic-code&state=synthetic-state");
  const calls = mockApi({ "POST /api/teams/callback": connected });
  renderAt("/teams/callback", [
    { path: "/teams/callback", element: <StrictMode><TeamsCallback /></StrictMode> },
    { path: "/preferences", element: <p>My preferences</p> },
  ]);
  expect(await screen.findByText("My preferences")).toBeInTheDocument();
  const posts = calls.filter(c => c.method === "POST");
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toEqual({ state: "synthetic-state", code: "synthetic-code", error: null });
  expect(window.location.hash).toBe("");
});

test("Teams reply sends only explicitly entered text and records completion", async () => {
  const done = vi.fn();
  const calls = mockApi({
    "GET /api/teams/connection": connected,
    "GET /api/tickets/4821/teams/chats": [{ id: "private-chat", recipient: "requester@example.com" }],
    "POST /api/tickets/4821/teams/messages": { comment_id: 12 },
  });
  renderAt("/", [{ path: "/", element: <TeamsReply ticketId={4821} onDone={done} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Message requester in Teams" }));
  await userEvent.selectOptions(await screen.findByLabelText("Teams recipient"), "private-chat");
  await userEvent.type(screen.getByLabelText("Teams message"), "Please reconnect your VPN.");
  expect(calls.filter(c => c.method === "POST")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Send Teams message" }));
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(calls.find(c => c.method === "POST")?.body).toEqual({
    body: "Please reconnect your VPN.", chat_id: "private-chat", delivery_id: expect.any(String),
  });
  expect(screen.getByText("Sent in Teams and saved on this ticket.")).toBeInTheDocument();
});

test("uncertain Teams delivery blocks retries until the technician checks Teams", async () => {
  const calls = mockApi({
    "GET /api/teams/connection": connected,
    "GET /api/tickets/4821/teams/chats": [{ id: "private-chat", recipient: "requester@example.com" }],
    "POST /api/tickets/4821/teams/messages": new Response(JSON.stringify({ detail: "Delivery not confirmed" }), { status: 409 }),
  });
  renderAt("/", [{ path: "/", element: <TeamsReply ticketId={4821} onDone={vi.fn()} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "Message requester in Teams" }));
  await userEvent.selectOptions(await screen.findByLabelText("Teams recipient"), "private-chat");
  await userEvent.type(screen.getByLabelText("Teams message"), "Hello");
  await userEvent.click(screen.getByRole("button", { name: "Send Teams message" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Delivery not confirmed");
  expect(screen.getByRole("button", { name: "Send Teams message" })).toBeDisabled();
  expect(calls.filter(c => c.method === "POST")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "I checked Teams — prepare a new send" }));
  expect(screen.getByRole("button", { name: "Send Teams message" })).toBeEnabled();
});
