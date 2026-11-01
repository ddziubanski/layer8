// SPDX-License-Identifier: AGPL-3.0-or-later
import { QueryClient } from "@tanstack/react-query";

import { refreshFor } from "./live";

test("a ticket change refetches the queue, counts and that ticket; a device change only that device", () => {
  const client = new QueryClient();
  const spy = vi.spyOn(client, "invalidateQueries");
  refreshFor(client, [{ t: "ticket", id: 4821 }, { t: "device", id: 7 }]);
  const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
  expect(keys).toEqual(['["tickets"]', '["views"]', '["ticket",4821]', '["device",7]']);
  spy.mockClear();
  refreshFor(client, [{ t: "device", id: 7 }]);
  expect(spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey))).toEqual(['["device",7]']);
});
