// SPDX-License-Identifier: AGPL-3.0-or-later
import { MutationCache, QueryCache, QueryClient, type Query } from "@tanstack/react-query";

import { ApiError } from "./api/client";

export const ME_KEY = ["me"];

export function createQueryClient(): QueryClient {
  // A 401 from anything else means the session ended: re-check who we are, which shows the
  // login page. Never for the "who am I" check itself, or a signed-out page loops forever.
  function onSessionLost(error: Error, query?: Query<unknown, unknown, unknown>) {
    if (!(error instanceof ApiError) || error.status !== 401) return;
    if (query && query.queryKey[0] === ME_KEY[0]) return;
    client.invalidateQueries({ queryKey: ME_KEY });
  }

  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error, query) => onSessionLost(error, query) }),
    mutationCache: new MutationCache({ onError: (error) => onSessionLost(error) }),
    defaultOptions: {
      queries: {
        // Don't hammer the API with retries for "not signed in" or "not found".
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
        refetchOnWindowFocus: true,
      },
    },
  });
  return client;
}
