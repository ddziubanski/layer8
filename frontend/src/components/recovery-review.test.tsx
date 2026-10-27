// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from "@testing-library/react";
import { TicketPriorityChip } from "./Chips";
import { ticket } from "../test/util";

test("confirmed recovery requests verification while preserving the actual priority", () => {
  const t = ticket({ priority: "high", recovery_review: true });
  const view = render(<TicketPriorityChip ticket={t} />);
  expect(screen.getByLabelText("Verify recovery")).toHaveTextContent("Verify");
  expect(screen.getByLabelText("Verify recovery")).toHaveAttribute("title", expect.stringContaining("priority remains High"));
  view.rerender(<TicketPriorityChip ticket={{ ...t, recovery_review: false }} />);
  expect(screen.getByText("High")).toBeInTheDocument();
  view.rerender(<TicketPriorityChip ticket={{ ...t, status: "closed" }} />);
  expect(screen.getByText("High")).toBeInTheDocument();
});
