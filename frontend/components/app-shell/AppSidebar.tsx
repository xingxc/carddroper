"use client";

import Link from "next/link";
import { brand } from "@/config/brand";
import { ProfileMenu } from "@/components/app-shell/ProfileMenu";

function BrandMark() {
  return (
    <Link
      href="/app"
      aria-label="Go to app home"
      className="w-10 h-10 rounded-lg bg-gray-800 text-white flex items-center justify-center text-lg font-bold hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
      {brand.name[0]}
    </Link>
  );
}

export function AppSidebar({ drawerOpen }: { drawerOpen: boolean }) {
  return (
    <aside
      id="app-sidebar"
      className={`fixed inset-y-0 left-0 w-16 z-30 flex flex-col items-center justify-between py-4 bg-gray-50 border-r border-gray-200 transition-transform duration-200 ease-in-out md:translate-x-0 ${drawerOpen ? "translate-x-0" : "-translate-x-full"}`}
    >
      <BrandMark />
      {/* Middle spacer — future feature icons slot here */}
      <div className="flex-1" />
      <ProfileMenu />
    </aside>
  );
}
