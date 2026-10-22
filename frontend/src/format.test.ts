import { duration, fileSize, slaInfo } from "./format";
import { describe as describeEvent } from "./pages/ticket/history";

const NOW = Date.parse("2026-10-18T10:00:00Z");
const at = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();

test("duration", () => {
  expect(duration(42 * 60_000)).toBe("0h 42m");
  expect(duration((3 * 60 + 10) * 60_000)).toBe("3h 10m");
  expect(duration((28 * 60 + 5) * 60_000)).toBe("1d 4h");
  expect(duration(-5)).toBe("0h 00m");
});

test("sla states", () => {
  expect(slaInfo({ sla_state: "ok", resolution_due_at: at(190) }, NOW)).toEqual({ tone: "g", short: "3h 10m", long: "SLA breach in 3h 10m" });
  expect(slaInfo({ sla_state: "at_risk", resolution_due_at: at(42) }, NOW).tone).toBe("a");
  expect(slaInfo({ sla_state: "breached", resolution_due_at: at(-65) }, NOW)).toEqual({ tone: "r", short: "-1h 05m", long: "SLA breached 1h 05m ago" });
  expect(slaInfo({ sla_state: "met", resolution_due_at: at(-5) }, NOW).long).toBe("SLA met");
  expect(slaInfo({ sla_state: "none", resolution_due_at: null }, NOW).short).toBe("None");
});

test("file sizes", () => {
  expect(fileSize(900)).toBe("900 B");
  expect(fileSize(214 * 1024)).toBe("214 KB");
  expect(fileSize(5.5 * 1024 * 1024)).toBe("5.5 MB");
});

test("history lines", () => {
  const names = { queue: (id: number | null | undefined) => (id === 2 ? "Network" : "Service desk"), user: () => "D. Cole" };
  const ev = (type: string, data: object) =>
    ({ id: 1, type, data, actor: null, created_at: at(0) }) as Parameters<typeof describeEvent>[0];
  expect(describeEvent(ev("routed", { rule_name: "switch", queue_name: "Network" }), names)).toBe('Routed to Network by rule "switch"');
  expect(describeEvent(ev("routed", { rule_name: null, queue_name: "Service desk" }), names)).toBe("Routed to Service desk (no rule matched)");
  expect(
    describeEvent(ev("assigned", { before: { queue_id: 1, assignee_id: null }, after: { queue_id: 2, assignee_id: 5 } }), names),
  ).toBe("Moved from Service desk to Network, assigned to D. Cole");
  expect(describeEvent(ev("status_changed", { before: "open", after: "waiting_on_user" }), names)).toBe("Status Open → Waiting on user");
  expect(describeEvent(ev("comment_added", { is_internal: true }), names)).toBe("Added an internal note");
});
