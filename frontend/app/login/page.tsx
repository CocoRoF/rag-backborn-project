"use client";
import clsx from "clsx";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button, Card, Field, Input } from "@/components/ui";
import { ApiError, get, markSession, post } from "@/lib/api";
import { useAuth, type User } from "@/stores/auth";

interface DemoAccount { email: string; password: string; role: string; label: string }
interface Status {
  bootstrap_needed: boolean; signup_mode: string; service_name: string; tagline: string;
  demo_accounts: DemoAccount[];
}

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next") || "/chat";
  const [status, setStatus] = useState<Status | null>(null);
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<Status>("/api/auth/status").then((s) => {
      setStatus(s);
      if (s.bootstrap_needed) setMode("signup");
    }).catch(() => setStatus(null));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const data = await post<{ access_token: string; user: User }>(path, { email, password, name });
      useAuth.getState().setAuth(data.access_token, data.user);
      markSession(true);
      router.replace(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "요청에 실패했습니다");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <div className="w-full max-w-sm space-y-5">
        <div className="space-y-1.5 text-center">
          <div className="mx-auto grid size-11 place-items-center rounded-xl bg-ink text-lg font-bold text-white">R</div>
          <h1 className="text-xl font-semibold tracking-tight">{status?.service_name ?? "RAG Backborn"}</h1>
          <p className="text-[13px] text-[#8b949e]">{status?.tagline ?? "계층형 파일 저장소 위의 RAG 백본"}</p>
        </div>
        <Card className="p-5">
          {status?.bootstrap_needed && (
            <p className="mb-4 rounded-lg bg-accent-soft px-3 py-2 text-[13px] text-accent">
              첫 계정입니다. 가입하면 관리자가 됩니다.
            </p>
          )}
          <form onSubmit={submit} className="space-y-3.5">
            {mode === "signup" && (
              <Field label="이름"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="홍길동" /></Field>
            )}
            <Field label="이메일">
              <Input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
            </Field>
            <Field label="비밀번호" hint={mode === "signup" ? "8자 이상" : undefined}>
              <Input type="password" required autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
            <Button type="submit" busy={busy} className="w-full">{mode === "login" ? "로그인" : "가입하기"}</Button>
          </form>
          {!!status?.demo_accounts?.length && (
            <div className="mt-4 border-t border-line pt-4">
              <p className="mb-2 text-[12px] font-medium text-[#57606a]">데모 계정 — 눌러서 바로 입력</p>
              <div className="space-y-1.5">
                {status.demo_accounts.map((a) => (
                  <button key={a.email} type="button"
                    onClick={() => { setMode("login"); setEmail(a.email); setPassword(a.password); setError(""); }}
                    className="flex w-full items-center gap-2 rounded-lg border border-line px-2.5 py-2 text-left transition-colors hover:bg-muted">
                    <span className={clsx("rounded-full px-2 py-0.5 text-[10.5px] font-medium",
                      a.role === "admin" ? "bg-accent-soft text-accent" : "bg-muted text-[#57606a]")}>
                      {a.role === "admin" ? "관리자" : "일반"}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[12.5px]">{a.email}</span>
                      <span className="block truncate font-mono text-[11.5px] text-[#8b949e]">{a.password}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-[#c1c7cd]">
                공개 데모용 계정입니다. 실제 운영에서는 <code className="text-[10.5px]">RAGB_DEMO_MODE=0</code> 으로 끄세요.
              </p>
            </div>
          )}

          {!status?.bootstrap_needed && status?.signup_mode === "open" && (
            <button onClick={() => { setMode(mode === "login" ? "signup" : "login"); setError(""); }}
              className="mt-3 w-full text-center text-[13px] text-[#8b949e] hover:text-accent">
              {mode === "login" ? "계정이 없으신가요? 가입하기" : "이미 계정이 있으신가요? 로그인"}
            </button>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense fallback={null}><LoginForm /></Suspense>;
}
