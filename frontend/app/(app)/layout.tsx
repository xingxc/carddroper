"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth";
import { LoadingScreen } from "@/components/loading/LoadingScreen";
import { AppSidebar } from "@/components/app-shell/AppSidebar";

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isLoading, isAuthenticated } = useAuth();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  // Pre-decision: show blurry screen until auth resolves or redirect fires.
  if (isLoading || !isAuthenticated) return <LoadingScreen />;

  return (
    <div className="min-h-screen">
      <AppSidebar />
      <main className="ml-16 px-6 py-4">{children}</main>
    </div>
  );
}
