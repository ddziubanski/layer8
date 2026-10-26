// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ME, mockApi, renderAt } from "../test/util";
import { Action1RemoteControl } from "./Action1RemoteControl";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const me = { ...ME, features: ["action1", "action1_remote"], permissions: ["action1_remote"] };
const viewerUrl = "https://us.remote.app.action1.com/remote/?id=synthetic-sensitive-link";

function setup(over: Record<string, unknown> = {}, identity = me) {
  const calls = mockApi({
    "POST /api/tickets/4821/action1-remote": { session_id: 7, status: "pending", message: "Waiting" },
    "GET /api/tickets/4821/action1-remote/7": { session_id: 7, status: "ready", message: "Ready", launch_url: viewerUrl },
    ...over,
  });
  renderAt("/", [{ path: "/", element: <Action1RemoteControl me={identity} ticketId={4821} deviceId={5} /> }]);
  return calls;
}

function popup() {
  const viewer = { opener: window, closed: false, document: document.implementation.createHTMLDocument(), location: { replace: vi.fn() }, close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(viewer as unknown as Window);
  return viewer;
}

test("one click requests assistance and opens the ready viewer without displaying its URL", async () => {
  const viewer = popup(); const calls = setup();
  await userEvent.click(screen.getByRole("button", { name: "Action1 remote desktop" }));
  await waitFor(() => expect(viewer.location.replace).toHaveBeenCalledWith(viewerUrl));
  expect(viewer.opener).toBeNull();
  expect(viewer.document.querySelector('meta[name="referrer"]')?.getAttribute("content")).toBe("no-referrer");
  expect(calls.filter(c => c.method === "POST")).toEqual([{ method: "POST", path: "/api/tickets/4821/action1-remote", body: { device_id: 5 } }]);
  expect(document.body.textContent).not.toContain("synthetic-sensitive-link");
  expect(viewer.close).not.toHaveBeenCalled();
});

test("blocked popup prevents any external launch request", async () => {
  vi.spyOn(window, "open").mockReturnValue(null); const calls = setup();
  await userEvent.click(screen.getByRole("button", { name: "Action1 remote desktop" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("No session was requested");
  expect(calls).toHaveLength(0);
});

test("unknown acceptance closes the placeholder and never retries or polls", async () => {
  const viewer = popup();
  const calls = setup({ "POST /api/tickets/4821/action1-remote": { session_id: 7, status: "unknown", message: "Check Action1 before starting another." } });
  await userEvent.click(screen.getByRole("button", { name: "Action1 remote desktop" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Check Action1");
  expect(viewer.close).toHaveBeenCalledOnce(); expect(viewer.location.replace).not.toHaveBeenCalled();
  expect(calls).toHaveLength(1);
});

test("provider errors close the placeholder without navigating", async () => {
  const viewer = popup();
  setup({ "GET /api/tickets/4821/action1-remote/7": new Response(JSON.stringify({ detail: "Action1 is unavailable" }), { status: 502 }) });
  await userEvent.click(screen.getByRole("button", { name: "Action1 remote desktop" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Action1 is unavailable");
  expect(viewer.close).toHaveBeenCalledOnce(); expect(viewer.location.replace).not.toHaveBeenCalled();
});

test.each(["feature", "permission"])("hidden without %s", boundary => {
  const calls = setup({}, { ...me, ...(boundary === "feature" ? { features: [] } : { permissions: [] }) });
  expect(screen.queryByRole("button")).not.toBeInTheDocument(); expect(calls).toHaveLength(0);
});
