// SPDX-License-Identifier: AGPL-3.0-or-later
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const html = readFileSync(new URL("../../../install/files/maintenance.html",import.meta.url),"utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)![1];

test("maintenance retries failed and HTML responses, then reloads the original URL only after JSON readiness",async () => {
  const button = {disabled:false,addEventListener:vi.fn()}, status = {textContent:""};
  const reload = vi.fn(), timers = new Map<number,{fn:()=>void,ms:number}>();
  let sequence = 0, state = "offline";
  const fetch = vi.fn(async (path:string,options:RequestInit) => {
    expect(options.cache).toBe("no-store");
    expect(options.credentials).toBe("same-origin");
    if(state === "offline") return new Response("",{status:503});
    if(state === "html") return new Response("<html>still unavailable</html>");
    return new Response(JSON.stringify(path === "/api/health" ? {status:"ok"} : {version:"9.0.0"}));
  });
  runInNewContext(script,{
    document:{getElementById:(id:string) => id === "retry" ? button : status},
    window:{location:{reload}},fetch,AbortController,
    setTimeout:(fn:()=>void,ms:number) => {timers.set(++sequence,{fn,ms});return sequence;},
    clearTimeout:(id:number) => timers.delete(id),
  });
  const settle = async () => {for(let i=0;i<20;i++) await Promise.resolve();};
  await settle();
  expect(status.textContent).toContain("Still reconnecting");
  expect(reload).not.toHaveBeenCalled();
  const tick = async () => {
    const entry = [...timers.entries()].find(([,value]) => value.ms === 5000)!;
    timers.delete(entry[0]);entry[1].fn();await settle();
  };
  state="html";await tick();
  expect(reload).not.toHaveBeenCalled();
  state="ready";await tick();
  expect(reload).toHaveBeenCalledOnce();
  expect(status.textContent).toContain("Services are ready");
  expect(fetch.mock.calls.map(([path]) => path)).toEqual(["/api/health","/api/health","/api/health","/api/about"]);
  expect([...timers.values()]).toHaveLength(0);
});
