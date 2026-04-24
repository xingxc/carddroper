"use client";

import { useBalance } from "@/hooks/useBalance";

export function BalanceDisplay({ className }: { className?: string }) {
  const { data, isLoading, isError } = useBalance();
  if (isLoading) return <span className={className}>—</span>;
  if (isError || !data) return <span className={className}>—</span>;
  return <span className={className}>{data.formatted}</span>;
}
