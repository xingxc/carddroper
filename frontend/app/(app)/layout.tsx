"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth";
import { LoadingScreen } from "@/components/loading/LoadingScreen";
import { AppSidebar } from "@/components/app-shell/AppSidebar";

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isLoading, isAuthenticated } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 0016.2 redirect — preserved verbatim.
  useEffect(() => {
    if (!isLoading && !isAuthenticated) router.replace("/login");
  }, [isLoading, isAuthenticated, router]);

  // Escape closes the drawer.
  useEffect(() => {
    if (!drawerOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [drawerOpen]);

  // Body scroll lock while drawer is open on narrow screens.
  // The md: breakpoint in CSS handles wide-screen no-op; this guard just ensures
  // we don't leave the body locked after a resize-to-wide while drawer was open.
  useEffect(() => {
    if (!drawerOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [drawerOpen]);

  // 0016.5 pre-decision blur — preserved verbatim.
  if (isLoading || !isAuthenticated) return <LoadingScreen />;

  return (
    <div className="min-h-screen">
      {/* Hamburger — only visible below md */}
      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        aria-label="Open navigation"
        aria-expanded={drawerOpen}
        aria-controls="app-sidebar"
        className="md:hidden fixed top-3 left-3 z-40 inline-flex items-center justify-center w-10 h-10 rounded-md text-gray-700 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <svg
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>

      {/* Backdrop — only visible below md, only when drawerOpen */}
      {drawerOpen && (
        <div
          className="md:hidden fixed inset-0 z-20 bg-black/40"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />
      )}

      <AppSidebar drawerOpen={drawerOpen} />

      <main className="px-6 py-4 pt-16 md:pt-4 md:ml-16">{children}</main>
    </div>
  );
}
