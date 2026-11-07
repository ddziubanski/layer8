# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser-side stand-ins for dev/screenshots.py, for pages that talk to services the dev app
doesn't have. Made-up data only; nothing is changed on the server.

Devices > Deploy software talks to Action1. For that one shot (its address carries
"demo-action1") the browser answers those calls itself, and adds "action1" to the signed-in
admin's features so the tab shows.
"""

FAKE_ACTION1_JS = r"""
(() => {
  if (!location.search.includes("demo-action1")) return;
  const org = "org-demo";
  const pcs = Array.from({length: 12}, (_, i) => ({device_id: 9000 + i, name: `PC-${String(101 + i).padStart(4, "0")}`, os: "Windows 11 Pro", last_seen: null}));
  const pkg = (id, name, vendor, versions) => ({id, name, vendor, platform: "Windows", versions: versions.map(v => ({id: `${id}-${v}`, version: v}))});
  const catalog = [pkg("Viewer", "Example PDF Viewer", "Example Software", ["24.3.1", "24.2.0"]), pkg("Notes", "Example Notes", "Example Software", ["8.1.0"]),
                   pkg("Browser", "Example Browser", "Example Corp", ["131.0.2"])];
  const packages = [{package_id: "Viewer", package_name: "Example PDF Viewer", vendor: "Example Software", version_id: "Viewer-24.3.1", version: "24.3.1"},
                    {package_id: "Notes", package_name: "Example Notes", vendor: "Example Software", version_id: "Notes-8.1.0", version: "8.1.0"}];
  const now = Date.now(), iso = ms => new Date(ms).toISOString();
  const base = {ticket_id: null, organization: "Example Org", platform: "Windows", retry_minutes: "480", results: [], instance_id: null, schedule_id: null, submitted_at: null};
  const runs = [
    {...base, id: "r1", state: "warning", created_at: iso(now - 86400000), submitted_at: iso(now - 86400000), instance_id: "i1", run_at: null,
     targets: [{type: "EndpointGroup", id: "g1", name: "Front desk", members: 4}], members: [], member_count: 4, packages: [packages[1]],
     message: "Action1 reports warnings; check the machines below before treating them as installed. (1 pending, 3 success)",
     results: ["PC-0101", "PC-0102", "PC-0103", "PC-0104"].map((n, i) => ({endpoint: n, status: i === 3 ? "Pending" : "Success",
       time: i === 3 ? "" : iso(now - 86000000), details: i === 3 ? "Offline: retried for 8 hours" : "Installed 8.1.0"}))},
    {...base, id: "r2", state: "scheduled", created_at: iso(now - 3600000), schedule_id: "s1", run_at: iso(now + 2 * 86400000),
     targets: [{type: "EndpointGroup", id: "g2", name: "New starters", members: 3}], members: [], member_count: 3, packages,
     message: "Scheduled in Action1. Nothing is installed yet."},
  ];
  const review = {...base, id: "rv", state: "review", created_at: iso(now), run_at: null, packages,
    targets: pcs.slice(0, 3).map(m => ({type: "Endpoint", id: `e${m.device_id}`, name: m.name, device_id: m.device_id})),
    members: pcs.slice(0, 3).map((m, i) => ({name: m.name, connection: i === 2 ? "Disconnected" : "Connected"})), member_count: 3,
    message: "Review these machines, packages and time. Nothing has been sent to Action1."};
  const answers = {
    "GET /api/action1/deploy/organizations": [{id: org, name: "Example Org"}],
    [`GET /api/action1/deploy/organizations/${org}/machines`]: pcs,
    [`GET /api/action1/deploy/organizations/${org}/groups`]: [{id: "g1", name: "Front desk", description: ""}, {id: "g2", name: "New starters", description: ""}],
    [`GET /api/action1/deploy/organizations/${org}/software`]: {items: catalog, more: false, offset: 0},
    "GET /api/action1/deploy/bundles": [{id: 1, organization_id: org, name: "New starter", platform: "Windows", packages, updated_at: iso(now)}],
    "GET /api/action1/batches": runs,
    "POST /api/action1/batches/preview": review,
  };
  const real = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.origin);
    const key = `${(init.method || "GET").toUpperCase()} ${url.pathname}`;
    if (key in answers) return new Response(JSON.stringify(answers[key]), {status: 200, headers: {"Content-Type": "application/json"}});
    const res = await real(input, init);
    if (key === "GET /api/auth/me" && res.ok) {
      const me = await res.clone().json();
      me.features = [...new Set([...(me.features || []), "action1"])];
      return new Response(JSON.stringify(me), {status: 200, headers: {"Content-Type": "application/json"}});
    }
    return res;
  };
})();
"""

# Pick three machines and two packages, keep offline machines for 8 hours, then Review.
FILL_DEPLOY_JS = """(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const set = (el, value) => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set.call(el, value);
    el.dispatchEvent(new Event("input", {bubbles: true})); el.dispatchEvent(new Event("change", {bubbles: true}));
  };
  await wait(800);
  for (const name of ["PC-0101", "PC-0102", "PC-0103"]) {
    [...document.querySelectorAll("label.inline")].find(l => l.textContent.includes(name))?.querySelector("input")?.click();
    await wait(80);
  }
  set(document.querySelector('[aria-label="Search software"]'), "example");
  await wait(100);
  [...document.querySelectorAll("button")].find(b => b.textContent === "Search")?.click();
  await wait(900);
  for (const b of [...document.querySelectorAll("button")].filter(b => b.textContent === "Add").slice(0, 2)) { b.click(); await wait(150); }
  const retry = [...document.querySelectorAll("label.field")].find(l => l.textContent.includes("Keep trying offline"))?.querySelector("select");
  if (retry) set(retry, "480");
  await wait(300);
  [...document.querySelectorAll("button")].find(b => b.textContent === "Review")?.click();
  await wait(1200);
})()"""
