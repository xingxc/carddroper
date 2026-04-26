"use client";

import { SubscribeForm } from "@/components/billing/SubscribeForm";
import { SubscriptionDisplay } from "@/components/billing/SubscriptionDisplay";

export default function SubscribePage() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-2">Subscription</h1>
      <SubscriptionDisplay className="mb-6 text-sm text-gray-600" />
      <SubscribeForm tiers={[]} />
    </div>
  );
}
