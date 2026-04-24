"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/context/auth";
import { LogoutButton } from "@/components/auth/LogoutButton";

export function ProfileMenu() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Click-outside detection — wraps both trigger and panel so clicking the
  // trigger doesn't immediately close the menu it just opened.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Escape key to close.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  const initial =
    user?.email != null && user.email.length > 0
      ? user.email.charAt(0).toUpperCase()
      : "?";

  return (
    <div ref={menuRef}>
      {/* Profile avatar trigger */}
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Account menu"
        className="w-8 h-8 rounded-full bg-gray-200 text-gray-700 flex items-center justify-center text-sm font-medium hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        {initial}
      </button>

      {/* Popover panel */}
      {open && (
        <div
          role="menu"
          className="fixed bottom-4 left-20 z-30 w-64 rounded-lg bg-white shadow-lg border border-gray-200 py-2"
        >
          {/* Email display */}
          <div className="px-4 py-2 text-sm text-gray-900 truncate">
            {user?.email ?? ""}
          </div>

          <hr className="my-1 border-gray-200" />

          {/* Settings section label — children added per feature ticket */}
          <div className="px-4 py-1 text-xs font-semibold uppercase text-gray-500">
            Settings
          </div>

          <Link
            href="/app/billing"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
          >
            Billing
          </Link>

          <hr className="my-1 border-gray-200" />

          {/* Logout action */}
          <div role="menuitem">
            <LogoutButton className="block w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50" />
          </div>
        </div>
      )}
    </div>
  );
}
