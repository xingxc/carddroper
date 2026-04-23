import { Suspense } from "react";
import { VerifyEmailBody } from "./VerifyEmailBody";
import { LoadingScreen } from "@/components/loading/LoadingScreen";

/**
 * Shell page — wraps the client component that reads `useSearchParams()` in a
 * Suspense boundary, which Next.js 16 requires for all `useSearchParams` callers.
 */
export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <VerifyEmailBody />
    </Suspense>
  );
}
