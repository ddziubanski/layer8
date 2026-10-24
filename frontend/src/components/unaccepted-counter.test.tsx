// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen } from "@testing-library/react";

import { mockApi, renderAt } from "../test/util";
import { UnacceptedCounter } from "./UnacceptedCounter";

afterEach(() => vi.unstubAllGlobals());

test("the top bar shows tickets nobody has taken, linking to Unassigned", async () => {
  mockApi({ "GET /api/tickets/unaccepted": { enabled: true, count: 3, minutes: 30 } });
  renderAt("/", [{ path: "/", element: <UnacceptedCounter /> }]);
  const link = await screen.findByRole("link", { name: "3 not taken" });
  expect(link).toHaveAttribute("href", "/?view=unassigned");
});

test("nothing when it's off or nothing waits", async () => {
  mockApi({ "GET /api/tickets/unaccepted": { enabled: true, count: 0, minutes: 30 } });
  const { container } = renderAt("/", [{ path: "/", element: <UnacceptedCounter /> }]);
  await new Promise((r) => setTimeout(r, 50));
  expect(container.textContent).toBe("");
});
