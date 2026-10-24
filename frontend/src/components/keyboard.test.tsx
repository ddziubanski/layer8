// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, Outlet, useSearchParams } from "react-router";
import { mockApi, renderAt } from "../test/util";
import { GroupPicker } from "./GroupPicker";
import { UserPicker } from "./UserPicker";
import { RouteFocus } from "./RouteFocus";

afterEach(() => vi.unstubAllGlobals());

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

test("AD groups support arrows, Escape and Enter without submitting the form", async () => {
  const groups = [{ dn: "CN=One,DC=corp,DC=example", name: "One" }, { dn: "CN=Two,DC=corp,DC=example", name: "Two" }];
  mockApi({ "GET /api/ad/groups": groups });
  const pick = vi.fn(), submit = vi.fn();
  wrap(<form onSubmit={(e) => { e.preventDefault(); submit(); }}><GroupPicker label="Group" onPick={pick} /></form>);
  const box = screen.getByRole("combobox", { name: "Group" });
  await userEvent.type(box, "gr");
  await screen.findByRole("option", { name: /One/ });
  await userEvent.keyboard("{ArrowDown}");
  expect(box).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: /Two/ }).id);
  await userEvent.keyboard("{Escape}");
  expect(box).toHaveAttribute("aria-expanded", "false");
  expect(pick).not.toHaveBeenCalled();
  await userEvent.keyboard("{ArrowDown}{Enter}");
  expect(pick).toHaveBeenCalledWith(groups[0]);
  expect(submit).not.toHaveBeenCalled();
});

test("people picker closes on Escape and announces its active result", async () => {
  mockApi({ "GET /api/users": [{ id: 8, display_name: "Example person", email: "person@example.com" }] });
  const pick = vi.fn();
  wrap(<UserPicker label="Requester" value={null} onChange={pick} />);
  const box = screen.getByRole("combobox", { name: "Requester" });
  await userEvent.type(box, "ex");
  const option = await screen.findByRole("option");
  expect(box).toHaveAttribute("aria-activedescendant", option.id);
  await userEvent.keyboard("{Escape}");
  expect(box).not.toHaveAttribute("aria-activedescendant");
  await userEvent.keyboard("{ArrowDown}{Enter}");
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({ id: 8 }));
});

test("route navigation focuses the new heading, while filter changes retain focus", async () => {
  function Page() { const [, setParams] = useSearchParams(); return <><h1>Second workspace</h1><button onClick={() => setParams({ filter: "mine" })}>Filter</button><input aria-label="Search" /></>; }
  renderAt("/one", [{ path: "/", element: <><RouteFocus mainId="test-main" /><main id="test-main" tabIndex={-1}><Outlet /></main></>, children: [
    { path: "one", element: <><h1>First</h1><Link to="/two">Next workspace</Link></> },
    { path: "two", element: <Page /> },
  ] }]);
  await userEvent.click(screen.getByRole("link", { name: "Next workspace" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Second workspace" })).toHaveFocus());
  await userEvent.click(screen.getByRole("button", { name: "Filter" }));
  expect(screen.getByRole("button", { name: "Filter" })).toHaveFocus();
  await userEvent.type(screen.getByLabelText("Search"), "example");
  expect(screen.getByLabelText("Search")).toHaveFocus();
});
