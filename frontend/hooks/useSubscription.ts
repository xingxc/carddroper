import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface SubscriptionResponse {
  has_subscription: boolean;
  tier_key: string | null;
  tier_name: string | null;
  status: string | null;
  current_period_end: string | null; // ISO-8601
  cancel_at_period_end: boolean;
}

export const SUBSCRIPTION_QUERY_KEY = ["billing", "subscription"] as const;

export function useSubscription(): UseQueryResult<SubscriptionResponse> {
  return useQuery<SubscriptionResponse>({
    queryKey: SUBSCRIPTION_QUERY_KEY,
    queryFn: () => api.get<SubscriptionResponse>("/billing/subscription"),
    staleTime: 30_000,
    retry: false,
  });
}
