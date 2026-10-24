// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt, type Call } from "./util";
import { TicketToolsWorkspace, DEFAULT_TICKET_WORKSPACE } from "../pages/ticket/TicketToolsWorkspace";
const layout = { top: [], main: [], side: [], bottom: [], hidden: [], fields: {} };
const panels = {
  conversation: { label: "Conversation", node: <textarea aria-label="Reply draft" /> },
  checklists: { label: "Checklist", node: <input aria-label="Checklist draft" /> },
  history: { label: "History", node: <p>Audit trail</p> },
  status: { label: "Status", node: <p>Status controls</p> },
  scripts: { label: "Scripts", node: <input aria-label="Script argument" /> },
};
function setup(hidden: string[] = []) {
  const calls = mockApi({ "PUT /api/auth/me/preferences": ({ body }: Call) => ({ ...ME, ticket_workspace: (body as {ticket_workspace: typeof DEFAULT_TICKET_WORKSPACE}).ticket_workspace }) });
  renderAt("/", [{ path: "/", element: <TicketToolsWorkspace me={{ ...ME, ticket_workspace: DEFAULT_TICKET_WORKSPACE }} panels={panels} layout={{ ...layout, hidden }} request={null} /> }]);
  return calls;
}
test("tabs and closed drawer retain drafts; tools mount lazily", async () => {
  const user = userEvent.setup(); setup();
  expect(screen.queryByLabelText("Script argument")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Reply draft"), "Keep reply");
  await user.click(screen.getByRole("tab", { name: "Work" }));
  await user.type(screen.getByLabelText("Checklist draft"), "In progress");
  await user.click(screen.getByRole("tab", { name: "History" }));
  await user.click(screen.getByRole("tab", { name: "Conversation" }));
  expect(screen.getByLabelText("Reply draft")).toHaveValue("Keep reply");
  await user.click(screen.getByRole("button", { name: /Tools/ }));
  await user.click(screen.getByRole("tab", { name: /Scripts/ }));
  await user.type(screen.getByLabelText("Script argument"), "argument");
  await user.click(screen.getByRole("tab", { name: /Details/ }));
  await user.click(screen.getByRole("tab", { name: /Scripts/ }));
  expect(screen.getByLabelText("Script argument")).toHaveValue("argument");
  fireEvent.keyDown(screen.getByLabelText("Script argument"), { key: "Escape" });
  expect(screen.getByRole("button", { name: /Tools/ })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: /Tools/ }));
  expect(screen.getByLabelText("Script argument")).toHaveValue("argument");
});
test("hidden panels stay unavailable and keyboard navigation works", async () => {
  const user = userEvent.setup(); setup(["scripts", "history"]);
  expect(screen.queryByRole("tab", { name: "History" })).not.toBeInTheDocument();
  screen.getByRole("tab", { name: "Conversation" }).focus();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Work" })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: /Tools/ }));
  expect(screen.queryByRole("tab", { name: /Scripts/ })).not.toBeInTheDocument();
});
test("only personal presentation preferences are saved", async () => {
  const user = userEvent.setup(); const calls = setup();
  await user.click(screen.getByRole("button", { name: /Tools/ }));
  await user.click(screen.getByRole("checkbox", { name: /Keep panel open/ }));
  await user.click(screen.getByRole("tab", { name: /Scripts/ }));
  await waitFor(() => expect(calls.filter(c => c.method === "PUT")).toHaveLength(2));
  expect(calls.at(-1)?.body).toEqual({ ticket_workspace: { ...DEFAULT_TICKET_WORKSPACE, keep_open: true, selected_tool: "scripts" } });
});

test("failed presentation saves can be retried", async () => {
  const user = userEvent.setup(); let failed = true;
  mockApi({ "PUT /api/auth/me/preferences": ({ body }: Call) => failed ? new Response(JSON.stringify({ detail: "Temporarily unavailable" }), { status: 503 }) : ({ ...ME, ticket_workspace: (body as { ticket_workspace: typeof DEFAULT_TICKET_WORKSPACE }).ticket_workspace }) });
  renderAt("/", [{ path: "/", element: <TicketToolsWorkspace me={{ ...ME, ticket_workspace: DEFAULT_TICKET_WORKSPACE }} panels={panels} layout={layout} request={null} /> }]);
  await user.click(screen.getByRole("button", { name: /Tools/ }));
  await user.click(screen.getByRole("tab", { name: /Scripts/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not saved");
  await user.type(screen.getByLabelText("Script argument"), "retained");
  failed = false; await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.getByLabelText("Script argument")).toHaveValue("retained");
});
