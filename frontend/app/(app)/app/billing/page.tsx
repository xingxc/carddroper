"use client";

import { BalanceDisplay } from "@/components/billing/BalanceDisplay";
import { TopupForm } from "@/components/billing/TopupForm";

export default function BillingPage() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-2">Billing</h1>
      <p className="text-sm text-gray-600 mb-6">
        Current balance:{" "}
        <BalanceDisplay className="font-medium text-gray-900" />
      </p>
      <div className="border-t border-gray-200 pt-6">
        <h2 className="text-lg font-semibold mb-4">Add funds</h2>
        <TopupForm />
      </div>
    </div>
  );
}
