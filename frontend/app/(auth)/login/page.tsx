import { Suspense } from "react";
import { LoginBody } from "./LoginBody";
import { LoadingScreen } from "@/components/loading/LoadingScreen";

/**
 * Shell page — wraps the client component that reads `useSearchParams()` in a
 * Suspense boundary, which Next.js requires for all `useSearchParams` callers.
 */
export default function LoginPage() {
  return (
    <Suspense fallback={<LoadingScreen />}>
      <LoginBody />
    </Suspense>
  );
}
