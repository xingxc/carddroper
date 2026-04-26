"use client";

import { useSubscription } from "@/hooks/useSubscription";

export function SubscriptionDisplay({ className }: { className?: string }) {
  const { data, isLoading, isError } = useSubscription();

  if (isLoading) return <span className={className}>—</span>;
  if (isError || !data) return <span className={className}>—</span>;

  if (!data.has_subscription) {
    return (
      <span className={className}>No active subscription.</span>
    );
  }

  const periodEnd = data.current_period_end
    ? new Date(data.current_period_end).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : null;

  return (
    <span className={className}>
      {data.tier_name ?? data.tier_key ?? "Unknown plan"}
      {" — "}
      {data.status}
      {periodEnd && `, next billing on ${periodEnd}`}
      {data.cancel_at_period_end && " (cancels at period end)"}
    </span>
  );
}
