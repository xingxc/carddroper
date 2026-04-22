"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useAuth } from "@/context/auth";
import { LogoutButton } from "@/components/auth/LogoutButton";
import { brand } from "@/config/brand";

function MarketingHeader() {
  const { user, isAuthenticated, isLoading } = useAuth();

  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
      <Link href="/" className="text-xl font-bold tracking-tight">
        {brand.name}
      </Link>

      <nav className="flex items-center gap-4">
        {isLoading ? (
          // Avoid layout shift while auth resolves.
          <span className="w-32 h-8 bg-gray-100 rounded animate-pulse" />
        ) : isAuthenticated ? (
          <>
            <span className="text-sm text-gray-600 truncate max-w-[200px]">
              {user?.email}
            </span>
            <LogoutButton className="text-sm text-gray-700 hover:text-gray-900 underline-offset-2 hover:underline" />
          </>
        ) : (
          <>
            <Link
              href="/login"
              className="text-sm text-gray-700 hover:text-gray-900"
            >
              Sign in
            </Link>
            <Link
              href="/register"
              className="text-sm font-medium bg-gray-900 text-white px-4 py-2 rounded-md hover:bg-gray-700"
            >
              Register
            </Link>
          </>
        )}
      </nav>
    </header>
  );
}

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <MarketingHeader />
      <main>{children}</main>
    </>
  );
}
