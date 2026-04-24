import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface BalanceResponse {
  balance_micros: number;
  formatted: string;
}

export const BALANCE_QUERY_KEY = ["billing", "balance"] as const;

export function useBalance(): UseQueryResult<BalanceResponse> {
  return useQuery<BalanceResponse>({
    queryKey: BALANCE_QUERY_KEY,
    queryFn: () => api.get<BalanceResponse>("/billing/balance"),
    staleTime: 30_000,
    retry: false,
  });
}
