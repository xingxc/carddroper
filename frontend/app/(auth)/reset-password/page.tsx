import { Suspense } from "react";
import { ResetPasswordBody } from "./ResetPasswordBody";
import { LoadingScreen } from "@/components/loading/LoadingScreen";

/**
 * Shell page — wraps the client component that reads `useSearchParams()` in a
 * Suspense boundary, which Next.js requires for all `useSearchParams` callers.
 */
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <ResetPasswordBody />
    </Suspense>
  );
}
