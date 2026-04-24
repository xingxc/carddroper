"use client";

import { ProfileMenu } from "@/components/app-shell/ProfileMenu";

export function AppSidebar({ drawerOpen }: { drawerOpen: boolean }) {
  return (
    <aside
      id="app-sidebar"
      className={`fixed inset-y-0 left-0 w-16 z-30 flex flex-col items-center py-4 bg-gray-50 border-r border-gray-200 transition-transform duration-200 ease-in-out md:translate-x-0 ${drawerOpen ? "translate-x-0" : "-translate-x-full"}`}
    >
      {/* Top slot — future feature icons land here (flex-1 pushes ProfileMenu to bottom) */}
      <div className="flex-1" />
      <ProfileMenu />
    </aside>
  );
}
