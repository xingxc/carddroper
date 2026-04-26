import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface TierEnvelope {
  lookup_key: string;
  tier_name: string;
  description: string | null;
  price_display: string;
  amount_cents: number;
  currency: string;
  interval: string;
  interval_count: number;
  grant_micros: number;
}

export const TIERS_QUERY_KEY = ["billing", "tiers"] as const;

export function useTiers(lookupKeys: string[]): UseQueryResult<TierEnvelope[]> {
  const sortedKeys = [...lookupKeys].sort();
  return useQuery<TierEnvelope[]>({
    queryKey: [...TIERS_QUERY_KEY, sortedKeys.join(",")],
    queryFn: () =>
      api.get<TierEnvelope[]>(
        `/billing/tiers?lookup_keys=${encodeURIComponent(sortedKeys.join(","))}`
      ),
    enabled: lookupKeys.length > 0,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
