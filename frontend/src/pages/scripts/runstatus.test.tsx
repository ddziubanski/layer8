import { render, screen } from "@testing-library/react";

import { busy, RunStatusChip } from "./Runs";

test("a queued run that waits for a date says so, and isn't polled for", () => {
  const later = new Date(Date.now() + 7 * 86_400_000).toISOString();
  render(<RunStatusChip status="queued" notBefore={later} />);
  expect(screen.getByText(/^Waits until /)).toBeInTheDocument();
  expect(busy({ status: "queued", not_before: later })).toBe(false);
  expect(busy({ status: "queued", not_before: new Date(Date.now() - 60_000).toISOString() })).toBe(true);
  expect(busy({ status: "running" })).toBe(true);
});
