// SPDX-License-Identifier: AGPL-3.0-or-later
import {screen,within,waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {ImportsAdmin} from "../pages/admin/ImportsAdmin";
import {ME,mockApi,renderAt} from "./util";

afterEach(()=>vi.unstubAllGlobals());
const report={applied:false,queue:"Imported IT",fingerprint:"a".repeat(64),counts:{tickets_created:3,internal_comments:2,attachments_created:1},warnings:["Ticket 42: no source creation timestamp; import time used for new records"]};

function setup(){
  const calls=mockApi({"GET /api/queues":[{id:1,name:"Imported IT",is_active:true},{id:2,name:"Facilities",is_active:true}],
    "POST /api/imports/kace/preview":report,"POST /api/imports/kace/apply":{...report,applied:true}});
  renderAt("/settings/imports",[{path:"/settings/imports",element:<ImportsAdmin me={{...ME,role:"admin"}} />}]);
  return calls;
}

test("preview is required before explicit apply and a changed queue invalidates review",async()=>{
  const calls=setup();const user=userEvent.setup();
  expect(screen.getByRole("button",{name:"Preview import"})).toBeDisabled();
  await screen.findByRole("option",{name:"Imported IT"});
  await user.selectOptions(await screen.findByLabelText("Target queue"),"1");
  await user.upload(screen.getByLabelText("KACE export bundle (.zip)"),new File(["zip"],"trial.zip",{type:"application/zip"}));
  expect(calls.every(call=>call.method==="GET")).toBe(true);
  await user.click(screen.getByRole("button",{name:"Preview import"}));
  const preview=await screen.findByRole("region",{name:"Import preview"});
  expect(within(preview).getByText(/no source creation timestamp/)).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Apply reviewed import"})).toBeDisabled();
  await user.click(screen.getByRole("checkbox",{name:/I reviewed this preview/}));
  await user.selectOptions(screen.getByLabelText("Target queue"),"2");
  expect(screen.queryByRole("region",{name:"Import preview"})).not.toBeInTheDocument();
  expect(calls.filter(call=>call.path.endsWith("/apply"))).toHaveLength(0);
  await user.click(screen.getByRole("button",{name:"Preview import"}));
  await screen.findByRole("region",{name:"Import preview"});
  await user.click(screen.getByRole("checkbox",{name:/I reviewed this preview/}));
  await user.click(screen.getByRole("button",{name:"Apply reviewed import"}));
  await screen.findByRole("heading",{name:"Import complete"});
  expect(screen.getByRole("link",{name:/Find KACE tickets/})).toHaveAttribute("href","/?q=KACE&scope=all&queue=2");
  const sent=calls.find(call=>call.path.endsWith("/apply"))!.body as FormData;
  expect(sent.get("fingerprint")).toBe(report.fingerprint);
  expect(sent.get("confirm")).toBe("true");expect(sent.get("queue_id")).toBe("2");
});

test("replacing a reviewed file removes confirmation and authorization remains admin-only",async()=>{
  setup();const user=userEvent.setup();
  await screen.findByRole("option",{name:"Imported IT"});
  await user.selectOptions(await screen.findByLabelText("Target queue"),"1");
  const input=screen.getByLabelText("KACE export bundle (.zip)");
  await user.upload(input,new File(["first"],"first.zip",{type:"application/zip"}));
  await user.click(screen.getByRole("button",{name:"Preview import"}));
  await screen.findByRole("heading",{name:"Review import"});
  await user.upload(input,new File(["second"],"second.zip",{type:"application/zip"}));
  expect(screen.queryByRole("button",{name:"Apply reviewed import"})).not.toBeInTheDocument();
});

test("agents never fetch queues or see import controls",()=>{
  const calls=mockApi({});renderAt("/",[{path:"/",element:<ImportsAdmin me={ME}/>}]);
  expect(screen.getByText("Imports are managed by admins.")).toBeInTheDocument();
  expect(calls).toHaveLength(0);
});

test("preview failures show an error without offering apply",async()=>{
  const user=userEvent.setup();mockApi({"GET /api/queues":[{id:1,name:"IT",is_active:true}],"POST /api/imports/kace/preview":()=>new Response('{"detail":"Invalid KACE export bundle"}',{status:422})});
  renderAt("/",[{path:"/",element:<ImportsAdmin me={{...ME,role:"admin"}}/>}]);
  await screen.findByRole("option",{name:"IT"});
  await user.selectOptions(await screen.findByLabelText("Target queue"),"1");
  await user.upload(screen.getByLabelText("KACE export bundle (.zip)"),new File(["bad"],"bad.zip",{type:"application/zip"}));
  await user.click(screen.getByRole("button",{name:"Preview import"}));
  await waitFor(()=>expect(screen.getByRole("alert")).toHaveTextContent("Invalid KACE export bundle"));
  expect(screen.queryByRole("button",{name:"Apply reviewed import"})).not.toBeInTheDocument();
});
