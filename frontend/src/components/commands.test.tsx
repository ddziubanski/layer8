// SPDX-License-Identifier: AGPL-3.0-or-later
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet } from "react-router";
import { ME, renderAt } from "../test/util";
import { CommandPalette, commandsFor } from "./CommandPalette";

function setup() {
  renderAt("/", [{ path: "/", element: <><CommandPalette me={ME} /><textarea aria-label="Work note" /><Outlet /></>, children: [
    { index: true, element: <h1>Queue</h1> }, { path: "devices", element: <h1>Device workspace</h1> }, { path: "tickets/:id", element: <h1>Ticket workspace</h1> },
  ] }]);
}

test("Ctrl K opens search, Escape restores focus, and Tab stays inside", async () => {
  setup();
  const note = screen.getByLabelText("Work note");
  await userEvent.click(note);
  await userEvent.keyboard("{Control>}k{/Control}");
  const box = screen.getByRole("combobox");
  expect(box).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
  await userEvent.tab({ shift: true });
  expect(box).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(note).toHaveFocus();
});

test("search and Enter navigate; ticket numbers use the existing ticket route", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: /Find a page/ }));
  await userEvent.type(screen.getByRole("combobox"), "devices");
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("heading", { name: "Device workspace" })).toBeInTheDocument();
  await userEvent.keyboard("{Meta>}k{/Meta}");
  await userEvent.type(screen.getByRole("combobox"), "#4821");
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("heading", { name: "Ticket workspace" })).toBeInTheDocument();
});

test("destinations respect admin and delegated settings permissions", () => {
  expect(commandsFor(ME).some(c => c.to === "/settings/users")).toBe(false);
  expect(commandsFor(ME).some(c => c.to === "/settings/rules")).toBe(false);
  expect(commandsFor({ ...ME, permissions: ["rules_manage"] }).some(c => c.to === "/settings/rules")).toBe(true);
  expect(commandsFor({ ...ME, role: "admin" }).some(c => c.to === "/settings/users")).toBe(true);
});

test("ordinary typing and other modal workflows are not intercepted", async () => {
  setup();
  await userEvent.type(screen.getByLabelText("Work note"), "keep this note");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  const existing = document.createElement("div"); existing.setAttribute("aria-modal", "true"); document.body.append(existing);
  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  existing.remove();
});

test("empty search results announce a status without navigating", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: /Find a page/ }));
  await userEvent.type(screen.getByRole("combobox"), "zzzzzzzz");
  expect(screen.getByRole("status")).toHaveTextContent("No matching destination");
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
});
