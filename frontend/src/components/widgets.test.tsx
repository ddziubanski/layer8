// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArrangedColumns } from "./Arrange";
import { api, type Layout } from "../api/client";
import { ME, mockApi, renderAt, type Call } from "../test/util";

const LAYOUT: Layout = { top: [], main: ["conversation"], side: ["status"], bottom: [], hidden: ["scripts", "copilot"], fields: {} };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function mount(editorOnly = true, admin = false) {
  const mounts = vi.fn();
  function Tool({id}: {id:string}) { useEffect(() => { mounts(id); }, []); return <input aria-label={`${id} draft`} />; }
  function Harness() {
    const [editing, setEditing] = useState(true);
    return <><button onClick={() => setEditing(true)}>Arrange again</button><ArrangedColumns page="ticket" editing={editing} onDone={() => setEditing(false)} editorOnly={editorOnly} layout={LAYOUT} required={["conversation"]} hasMine={false} me={{...ME,role:admin?"admin":"agent"}} panels={{conversation:{label:"Conversation",node:<Tool id="conversation" />},status:{label:"Status",node:<Tool id="status" />},scripts:{label:"Scripts",node:<Tool id="scripts" />},copilot:{label:"Copilot",node:null}}} /></>;
  }
  renderAt("/", [{path:"/",element:<Harness />}]);
  return mounts;
}

test("editor-only catalog never mounts live tools; search, availability and required widgets are respected", async () => {
  const calls = mockApi({}); const user = userEvent.setup(); const mounts = mount();
  expect(mounts).not.toHaveBeenCalled();
  expect(screen.getByRole("button",{name:"Remove Conversation widget"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"Add Copilot widget"})).toBeDisabled();
  await user.type(screen.getByRole("searchbox",{name:"Find a widget"}), "scripts");
  const library = screen.getByRole("list",{name:"Widget library"});
  expect(within(library).queryByText("Conversation",{exact:true})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Add Scripts widget"}));
  expect(screen.getByRole("group",{name:"Panel: Scripts"})).toBeInTheDocument();
  expect(mounts).not.toHaveBeenCalled(); expect(calls).toHaveLength(0);
  await user.click(screen.getByRole("button",{name:"Cancel"}));
  await user.click(screen.getByRole("button",{name:"Arrange again"}));
  expect(screen.queryByRole("group",{name:"Panel: Scripts"})).not.toBeInTheDocument();
});

test("essentials and placement stay a draft until saved; saves opt into future widgets", async () => {
  const user = userEvent.setup();
  const calls = mockApi({"PUT /api/layouts/ticket/mine": ({body}:Call) => ({effective:body,mine:body,required:["conversation"]})}); mount();
  await user.click(screen.getByRole("button",{name:"Start with essentials"}));
  expect(screen.queryByRole("group",{name:"Panel: Status"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Add Scripts widget"}));
  await user.selectOptions(screen.getByLabelText("Where Scripts goes"),"bottom");
  expect(calls).toHaveLength(0);
  await user.click(screen.getByRole("button",{name:"Save for me"}));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].body).toMatchObject({widget_mode:true,main:["conversation"],side:[],bottom:["scripts"],hidden:["copilot","status"]});
});

test("failed saves retain widget draft and support retry; shared defaults remain admin-only", async () => {
  const user = userEvent.setup(); let fail=true;
  const calls = mockApi({"PUT /api/layouts/ticket/mine": ({body}:Call) => fail ? new Response("{}",{status:503}) : {effective:body,mine:body,required:["conversation"]}}); mount();
  expect(screen.queryByRole("button",{name:"Save as everyone's default"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Add Scripts widget"}));
  await user.click(screen.getByRole("button",{name:"Save for me"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Layout was not saved");
  expect(screen.getByRole("group",{name:"Panel: Scripts"})).toBeInTheDocument();
  fail=false; await user.click(screen.getByRole("button",{name:"Save for me"}));
  await waitFor(() => expect(screen.queryByRole("region",{name:"Arrange this page"})).not.toBeInTheDocument());
  expect(calls).toHaveLength(2);
});

test("pending writes lock editor controls and admins may save shared defaults", async () => {
  mockApi({}); const user = userEvent.setup(); mount(true,true);
  let done!: (v:any)=>void;
  vi.spyOn(api.layouts,"saveDefault").mockImplementation(() => new Promise(resolve=>{done=resolve;}));
  await user.click(screen.getByRole("button",{name:"Save as everyone's default"}));
  expect(screen.getByRole("button",{name:"Cancel"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"Add Scripts widget"})).toBeDisabled();
  done({effective:LAYOUT,required:["conversation"]});
  await waitFor(() => expect(screen.queryByRole("region",{name:"Arrange this page"})).not.toBeInTheDocument());
});
