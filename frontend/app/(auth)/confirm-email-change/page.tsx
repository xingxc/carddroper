import { Suspense } from "react";
import { ConfirmEmailChangeBody } from "./ConfirmEmailChangeBody";
import { LoadingScreen } from "@/components/loading/LoadingScreen";

/**
 * Shell page — wraps the client component that reads `useSearchParams()` in a
 * Suspense boundary, which Next.js requires for all `useSearchParams` callers.
 */
export default function ConfirmEmailChangePage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <ConfirmEmailChangeBody />
    </Suspense>
  );
}
