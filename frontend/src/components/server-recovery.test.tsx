// SPDX-License-Identifier: AGPL-3.0-or-later
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import { ME_KEY } from "../queryClient";
import { ME, mockApi } from "../test/util";
import { applicationReload, serverReady, useServerRecovery } from "./ServerRecovery";
import { version } from "../../package.json";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); });

function harness() {
  let offline = false, expired = false, installed = version;
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const calls = mockApi({
    "GET /api/auth/me": () => offline ? new Response("{}",{status:503}) : expired ? new Response("{}",{status:401}) : ME,
    "GET /api/health": () => offline ? new Response("{}",{status:503}) : {status:"ok"},
    "GET /api/about": () => ({version:installed}),
  });
  function Workspace() {
    const me = useQuery({queryKey:ME_KEY,queryFn:api.me});
    const recovery = useServerRecovery(me.error,me.data,() => me.refetch());
    return <>{recovery.banner}<div inert={recovery.blocked || undefined}><textarea aria-label="Ticket draft" defaultValue="" />{me.error && !recovery.blocked && <p>Session expired</p>}</div></>;
  }
  render(<QueryClientProvider client={client}><Workspace /></QueryClientProvider>);
  return {client,calls,offline:() => {offline=true;return client.invalidateQueries({queryKey:ME_KEY});},online:(next=version,expire=false) => {offline=false;installed=next;expired=expire;}};
}

test("automatic readiness polling resumes the same mounted draft after a transient outage", async () => {
  const reload = vi.spyOn(applicationReload,"run").mockImplementation(() => {});
  const h = harness();
  await waitFor(() => expect(h.client.getQueryData(ME_KEY)).toEqual(ME));
  const draft = screen.getByLabelText("Ticket draft");
  fireEvent.input(draft,{target:{value:"Keep this reply"}});
  await act(async () => {await h.offline();});
  expect(await screen.findByText("Reconnecting to Layer 8")).toBeInTheDocument();
  expect(draft.parentElement).toHaveAttribute("inert");
  h.online();
  await waitFor(() => expect(screen.queryByText("Reconnecting to Layer 8")).not.toBeInTheDocument(),{timeout:7000});
  expect(screen.getByLabelText("Ticket draft")).toBe(draft);
  expect(draft).toHaveValue("Keep this reply");
  expect(draft.parentElement).not.toHaveAttribute("inert");
  expect(reload).not.toHaveBeenCalled();
},10000);

test.each([false,true])("a new version refreshes only when controls are clean (dirty=%s)",async dirty => {
  const reload = vi.spyOn(applicationReload,"run").mockImplementation(() => {});
  const h = harness();
  await waitFor(() => expect(h.client.getQueryData(ME_KEY)).toEqual(ME));
  if(dirty) fireEvent.input(screen.getByLabelText("Ticket draft"),{target:{value:"Unsaved reply"}});
  await act(async () => {await h.offline();});
  await screen.findByText("Reconnecting to Layer 8");
  h.online("9.0.0");
  fireEvent.click(screen.getByRole("button",{name:"Check connection now"}));
  await screen.findByText("Layer 8 9.0.0 is ready");
  expect(reload).toHaveBeenCalledTimes(dirty ? 0 : 1);
  if(dirty) {
    expect(screen.getByLabelText("Ticket draft")).toHaveValue("Unsaved reply");
    fireEvent.click(screen.getByRole("button",{name:"Load updated app (refresh page)"}));
    expect(reload).toHaveBeenCalledOnce();
  }
});

test("an expired session remains an authentication error after readiness succeeds",async () => {
  const h = harness();
  await waitFor(() => expect(h.client.getQueryData(ME_KEY)).toEqual(ME));
  await act(async () => {await h.offline();});
  await screen.findByText("Reconnecting to Layer 8");
  h.online(version,true);
  fireEvent.click(screen.getByRole("button",{name:"Check connection now"}));
  expect(await screen.findByText("Session expired")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText("Reconnecting to Layer 8")).not.toBeInTheDocument());
});

test("readiness rejects HTML and incomplete JSON rather than treating a 200 as success",async () => {
  mockApi({"GET /api/health": () => new Response("<html>maintenance</html>")});
  await expect(serverReady()).rejects.toThrow();
  mockApi({"GET /api/health":{status:"ok"},"GET /api/about":{version:""}});
  await expect(serverReady()).rejects.toThrow("Installed version");
});
