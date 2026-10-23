import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { mockApi, renderAt, ticket } from "../../test/util";
import { CopilotCard } from "./CopilotCard";

afterEach(() => vi.unstubAllGlobals());
const connected = { configured: true, connected: true, account: "agent@example.com", copilot: true };
const preview = { sections: ["Ticket text to share"], characters: 20, limit: 12000, digest: "a".repeat(64) };
const answer = { conversation_id: "conv-1", answer: "Re-seat the **uplink** cable.", citations: [{ title: "Switch runbook", url: "https://docs.example.com/runbook" }] };
const base = { "GET /api/teams/connection": connected, "GET /api/tickets/4821/copilot/context": preview, "POST /api/tickets/4821/copilot": answer };

async function askWithPreview() {
  await userEvent.type(await screen.findByLabelText("Ask Copilot"), "What fixes this?");
  expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Review ticket context" }));
  expect(await screen.findByText("Ticket text to share")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText("uplink")).toBeInTheDocument();
}

test("review exact context, retain follow-up and explicitly save a note with sources", async () => {
  const calls = mockApi({ ...base, "POST /api/tickets/4821/comments": { id: 1, emailed: false } });
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  await askWithPreview();
  expect(screen.getByRole("link", { name: "Switch runbook" })).toHaveAttribute("href", "https://docs.example.com/runbook");
  expect(calls.find(c => c.path.endsWith("/copilot"))?.body).toEqual({ question: "What fixes this?", include_ticket: true,
    include_internal: false, context_digest: preview.digest, conversation_id: null });
  expect(calls.filter(c => c.path.endsWith("/comments"))).toHaveLength(0);
  await userEvent.type(screen.getByLabelText("Ask a follow-up"), "And then?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(calls.filter(c => c.path.endsWith("/copilot"))).toHaveLength(2));
  expect(calls.filter(c => c.path.endsWith("/copilot"))[1].body).toMatchObject({ conversation_id: "conv-1" });
  const first = within(screen.getByRole("region", { name: "Copilot answer 1" }));
  await userEvent.click(first.getByLabelText("Include this response on the ticket"));
  await userEvent.click(first.getByRole("button", { name: "Add as an internal note" }));
  expect(await first.findByText("Added as an internal note.")).toBeInTheDocument();
  expect(calls.find(c => c.path.endsWith("/comments"))?.body).toMatchObject({ is_internal: true, email_requester: false });
  expect(String((calls.find(c => c.path.endsWith("/comments"))?.body as { body: string }).body)).toContain("https://docs.example.com/runbook");
});

test("send to requester requires both choices, an edited reply and the send button", async () => {
  const calls = mockApi({ ...base, "POST /api/tickets/4821/comments": { id: 1, emailed: true } });
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  await askWithPreview();
  expect(screen.getByLabelText("Send to requester")).toBeDisabled();
  await userEvent.click(screen.getByLabelText("Include this response on the ticket"));
  await userEvent.click(screen.getByLabelText("Send to requester"));
  await userEvent.clear(screen.getByLabelText("Reply to requester"));
  await userEvent.type(screen.getByLabelText("Reply to requester"), "Please reconnect your cable and let us know.");
  expect(calls.filter(c => c.path.endsWith("/comments"))).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Send reply" }));
  expect(await screen.findByText("Saved as a public reply; email queued for the requester.")).toBeInTheDocument();
  expect(calls.find(c => c.path.endsWith("/comments"))?.body).toEqual({ body: "Please reconnect your cable and let us know.", is_internal: false, email_requester: true });
});

test("saving failures are visible and do not trigger automatic retries", async () => {
  const calls = mockApi({ ...base, "POST /api/tickets/4821/comments": new Response(JSON.stringify({ detail: "Save failed" }), { status: 503 }) });
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  await askWithPreview();
  await userEvent.click(screen.getByLabelText("Include this response on the ticket"));
  await userEvent.click(screen.getByRole("button", { name: "Add as an internal note" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Save failed");
  expect(calls.filter(c => c.path.endsWith("/comments"))).toHaveLength(1);
  expect(screen.getByLabelText("Internal note response")).toHaveValue(answer.answer);
});

test("changing sharing starts a new conversation and excludes ticket context", async () => {
  const calls = mockApi(base);
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  await askWithPreview();
  await userEvent.click(screen.getByLabelText("Include this ticket's text"));
  await userEvent.type(screen.getByLabelText("Ask Copilot"), "New question");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(calls.filter(c => c.path.endsWith("/copilot"))).toHaveLength(2));
  expect(calls.filter(c => c.path.endsWith("/copilot"))[1].body).toMatchObject({ conversation_id: null, include_ticket: false, include_internal: false, context_digest: null });
});

test("pending questions and sharing controls cannot change until the answer arrives", async () => {
  let resolve!: (response: Response) => void;
  const pending = new Promise<Response>(r => { resolve = r; });
  vi.stubGlobal("fetch", async (path: string) => path.endsWith("/copilot") ? pending : new Response(JSON.stringify(connected)));
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  await userEvent.click(await screen.findByLabelText("Include this ticket's text"));
  await userEvent.type(screen.getByLabelText("Ask Copilot"), "Original question");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(screen.getByLabelText("Ask Copilot")).toBeDisabled();
  expect(screen.getByLabelText("Include this ticket's text")).toBeDisabled();
  await act(async () => resolve(new Response(JSON.stringify(answer))));
  expect(await screen.findByText("You asked: Original question")).toBeInTheDocument();
});

function Switcher() {
  const [id, setId] = useState(4821);
  const client = useQueryClient();
  return <><button onClick={() => setId(4822)}>Switch ticket</button>
    <button onClick={() => client.setQueryData(["teams-connection"], { ...connected, account: "other@example.com" })}>Switch Microsoft account</button>
    <CopilotCard ticket={ticket({ id })} onChanged={vi.fn()} /></>;
}

for (const button of ["Switch ticket", "Switch Microsoft account"]) {
  test(`${button} clears the prior conversation and answer state`, async () => {
    mockApi(base);
    renderAt("/", [{ path: "/", element: <Switcher /> }]);
    await askWithPreview();
    await userEvent.click(screen.getByRole("button", { name: button }));
    expect(screen.queryByText("uplink")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ask Copilot")).toHaveValue("");
  });
}

test("without Copilot permissions the panel says to reconnect", async () => {
  mockApi({ "GET /api/teams/connection": { ...connected, copilot: false } });
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={vi.fn()} /> }]);
  expect(await screen.findByRole("link", { name: /Reconnect your Microsoft account/ })).toHaveAttribute("href", "/preferences");
});
