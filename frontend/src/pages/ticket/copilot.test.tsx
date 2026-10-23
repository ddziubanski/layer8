import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt, ticket } from "../../test/util";
import { CopilotCard } from "./CopilotCard";

afterEach(() => vi.unstubAllGlobals());

const connected = { configured: true, connected: true, account: "jpark@corp.example", copilot: true };

test("ask Copilot with the ticket's text, see sources, keep the conversation and save an answer as a note", async () => {
  const calls = mockApi({
    "GET /api/teams/connection": connected,
    "POST /api/tickets/4821/copilot": { conversation_id: "conv-1", answer: "Re-seat the **uplink** cable.", citations: [{ title: "Switch runbook", url: "https://contoso.sharepoint.com/runbook.docx" }] },
    "POST /api/tickets/4821/comments": { id: 1 },
  });
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={() => undefined} /> }]);
  await userEvent.type(await screen.findByLabelText("Ask Copilot"), "What fixes port flapping?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText("uplink")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Switch runbook" })).toHaveAttribute("href", "https://contoso.sharepoint.com/runbook.docx");
  expect(calls.find((c) => c.path.endsWith("/copilot"))?.body).toEqual({ question: "What fixes port flapping?", include_ticket: true, conversation_id: null });
  await userEvent.type(screen.getByLabelText("Ask a follow-up"), "And if that fails?");
  await userEvent.click(screen.getByLabelText(/Include this ticket/));
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/copilot"))[1]?.body).toEqual({ question: "And if that fails?", include_ticket: false, conversation_id: "conv-1" }));
  await userEvent.click(screen.getAllByRole("button", { name: "Add as an internal note" })[0]);
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/comments"))).toBe(true));
});

test("without the Copilot permissions the panel says to reconnect", async () => {
  mockApi({ "GET /api/teams/connection": { ...connected, copilot: false } });
  renderAt("/", [{ path: "/", element: <CopilotCard ticket={ticket()} onChanged={() => undefined} /> }]);
  expect(await screen.findByRole("link", { name: /Reconnect your Microsoft account/ })).toHaveAttribute("href", "/preferences");
});
