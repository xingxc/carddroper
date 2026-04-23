"use client";

import Link from "next/link";
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth";
import { LogoutButton } from "@/components/auth/LogoutButton";
import { brand } from "@/config/brand";

function AppHeader() {
  const { user } = useAuth();

  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
      <Link href="/" className="text-xl font-bold tracking-tight">
        {brand.name}
      </Link>

      <nav className="flex items-center gap-4">
        {user && (
          <span className="text-sm text-gray-600 truncate max-w-[200px]">
            {user.email}
          </span>
        )}
        <LogoutButton className="text-sm text-gray-700 hover:text-gray-900 underline-offset-2 hover:underline" />
      </nav>
    </header>
  );
}

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isLoading, isAuthenticated } = useAuth();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  return (
    <>
      <AppHeader />
      <main>{children}</main>
    </>
  );
}
