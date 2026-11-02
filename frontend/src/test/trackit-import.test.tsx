// SPDX-License-Identifier: AGPL-3.0-or-later
import {screen,within} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {ImportsAdmin} from "../pages/admin/ImportsAdmin";
import {ME,mockApi,renderAt} from "./util";

afterEach(()=>vi.unstubAllGlobals());
const ID="a".repeat(32);
const fields=[
  {column:"OPENED_GROUP",name:"Opened by group",used:2,total:3,distinct:2,top:[["NETWORK",1],["Desktop",1]],becomes:"the queue"},
  {column:"DEPT",name:"Department",used:1,total:3,distinct:1,top:[["Finance",1]],becomes:"the department"},
  {column:"HD_LOOKUP01",name:"Lookup 1",used:1,total:3,distinct:1,top:[["Building A",1]],becomes:"an imported detail"},
];
const staged={id:ID,server:"TRACKITSRV",database:"TRACKIT",exported_at:"2026-10-20T10:00:00",since:null,size:2048,fields,state:"staged",apply:false};
const report={counts:{ticket_new:3},lines:["Dry run (nothing saved):","  tickets: 3 new, 0 updated"],problems:0};
const queues=[{id:1,name:"Desk",is_active:true},{id:2,name:"Desktop support",is_active:true}];

function render(path="/settings/imports"){
  renderAt(path,[{path:"/settings/imports",element:<ImportsAdmin me={{...ME,role:"admin"}} />}]);
}

test("upload, choose, dry run, and import only with the dry run's choices",async()=>{
  const calls=mockApi({"GET /api/queues":queues,"POST /api/imports/trackit":staged,
    [`POST /api/imports/trackit/${ID}/run`]:(call:{body:unknown})=>{
      const {apply,confirm:_c,...used}=call.body as Record<string,unknown>;
      return {...staged,state:"done",apply,used,report:{...report,lines:[apply?"Imported:":"Dry run (nothing saved):",...report.lines.slice(1)]}};
    }});
  const user=userEvent.setup();render();
  const panel=screen.getByRole("region",{name:"Track-It! ticket import"});
  await user.upload(within(panel).getByLabelText("Track-It! export bundle (.zip)"),new File(["zip"],"trackit.zip",{type:"application/zip"}));
  await user.click(within(panel).getByRole("button",{name:"Upload and check"}));
  expect(await within(panel).findByText("TRACKITSRV")).toBeInTheDocument();
  await within(panel).findAllByRole("option",{name:"Desktop support"});
  await user.selectOptions(within(panel).getByLabelText("Queue for Track-It! group Desktop"),"Desktop support");
  await user.selectOptions(within(panel).getByLabelText("HD_LOOKUP01: keep, skip or rename"),"rename");
  await user.type(within(panel).getByLabelText("New name for HD_LOOKUP01"),"Building");
  expect(within(panel).queryByRole("button",{name:"Import"})).not.toBeInTheDocument();
  await user.click(within(panel).getByRole("button",{name:"Dry run"}));
  expect(await within(panel).findByText(/tickets: 3 new/)).toBeInTheDocument();
  const dry=calls.find(c=>c.path.endsWith("/run"))!.body as Record<string,unknown>;
  expect(dry).toMatchObject({apply:false,groups:{Desktop:"Desktop support"},labels:{HD_LOOKUP01:"Building"},departments:"match",queue:null});
  // A changed choice needs another dry run.
  await user.selectOptions(within(panel).getByLabelText(/^Departments/),"add");
  expect(within(panel).queryByRole("button",{name:"Import"})).not.toBeInTheDocument();
  expect(within(panel).getByText(/choices changed since this dry run/)).toBeInTheDocument();
  await user.selectOptions(within(panel).getByLabelText(/^Departments/),"match");
  const importButton=within(panel).getByRole("button",{name:"Import"});
  expect(importButton).toBeDisabled();
  await user.click(within(panel).getByRole("checkbox",{name:/I reviewed this dry run/}));
  await user.click(importButton);
  expect(await within(panel).findByText(/the bundle was deleted from the server/)).toBeInTheDocument();
  const sent=calls.filter(c=>c.path.endsWith("/run"))[1].body as Record<string,unknown>;
  expect(sent).toMatchObject({apply:true,confirm:true,labels:{HD_LOOKUP01:"Building"}});
});

test("coming back to a dry-run bundle restores its choices",async()=>{
  const used={queue:"Desk",groups:{NETWORK:"Desk"},labels:{},skip:["HD_LOOKUP01"],departments:"skip"};
  mockApi({"GET /api/queues":queues,[`GET /api/imports/trackit/${ID}`]:{...staged,state:"done",apply:false,used,report}});
  const user=userEvent.setup();render(`/settings/imports?trackit=${ID}`);
  const panel=screen.getByRole("region",{name:"Track-It! ticket import"});
  await within(panel).findByText(/tickets: 3 new/);
  await within(panel).findAllByRole("option",{name:"Desk"});
  expect(within(panel).getByLabelText("HD_LOOKUP01: keep, skip or rename")).toHaveValue("skip");
  expect(within(panel).getByLabelText(/^Departments/)).toHaveValue("skip");
  await user.click(within(panel).getByRole("checkbox",{name:/I reviewed this dry run/}));
  expect(within(panel).getByRole("button",{name:"Import"})).toBeEnabled();
});
