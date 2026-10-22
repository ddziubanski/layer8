import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { PreferencesPage } from "./PreferencesPage";

afterEach(() => vi.unstubAllGlobals());

test("pick my colour and add a rule; saved together", async () => {
  const calls = mockApi({
    "GET /api/queues": [{ id: 2, name: "Network", is_active: true }],
    "PUT /api/auth/me/preferences": ({ body }: Call) => ({ ...ME, ...(body as object) }),
  });
  renderAt("/", [{ path: "/", element: <PreferencesPage me={ME} /> }]);
  await userEvent.click(within(screen.getByRole("group", { name: "My colour" })).getByRole("button", { name: "#059669" }));
  await userEvent.click(screen.getByRole("button", { name: "Add a rule" }));
  const rule = screen.getByRole("group", { name: "Rule 1" });
  await userEvent.selectOptions(within(rule).getByLabelText("When"), "queue");
  await userEvent.selectOptions(within(rule).getByLabelText("Value"), "2");
  await userEvent.click(within(within(rule).getByRole("group", { name: "Rule 1 colour" })).getByRole("button", { name: "#9333ea" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ ticket_color: "#059669", color_rules: [{ field: "queue", value: "2", color: "#9333ea" }] }),
  );
  expect(await screen.findByText("Saved.")).toBeInTheDocument();
});
