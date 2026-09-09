"use client";
import clsx from "clsx";
import { Database, LogOut, MessagesSquare, Settings2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { hadSession, post, refreshAccess } from "@/lib/api";
import { useAuth } from "@/stores/auth";

const NAV = [
  { href: "/chat", label: "채팅", icon: MessagesSquare },
  { href: "/storage", label: "RAG 저장소", icon: Database },
  { href: "/admin", label: "관리", icon: Settings2, adminOnly: true },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, ready } = useAuth();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    // The access token lives in memory only, so every load starts by trying the refresh
    // cookie. hadSession() keeps a first-time visitor from a guaranteed 401 on arrival.
    (async () => {
      if (useAuth.getState().token) { setChecked(true); return; }
      if (hadSession()) await refreshAccess();
      useAuth.getState().setReady(true);
      setChecked(true);
    })();
  }, []);

  useEffect(() => {
    if (checked && ready && !user) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [checked, ready, user, pathname, router]);

  if (!checked || !user) {
    return <div className="flex h-dvh items-center justify-center text-sm text-[#8b949e]">불러오는 중…</div>;
  }

  const logout = async () => {
    try { await post("/api/auth/logout"); } catch { /* already gone */ }
    useAuth.getState().clear();
    window.location.assign("/login");
  };

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center gap-1 border-b border-line bg-white px-4">
        <Link href="/chat" className="mr-4 flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-lg bg-ink text-[13px] font-bold text-white">R</span>
          <span className="text-[15px] font-semibold tracking-tight">RAG Backborn</span>
        </Link>
        <nav className="flex items-center gap-1">
          {NAV.filter((n) => !n.adminOnly || user.role === "admin").map((n) => {
            const active = pathname.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href} className={clsx(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
                active ? "bg-accent-soft text-accent" : "text-[#57606a] hover:bg-muted")}>
                <n.icon className="size-4" />{n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-[13px] text-[#57606a]">{user.name || user.email}</span>
          <button onClick={logout} title="로그아웃"
            className="rounded-lg p-1.5 text-[#8b949e] transition-colors hover:bg-muted hover:text-[#57606a]">
            <LogOut className="size-4" />
          </button>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
    </div>
  );
}
