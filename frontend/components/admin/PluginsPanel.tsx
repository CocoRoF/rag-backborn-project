"use client";
import { useEffect, useState } from "react";

import { Badge, Card, Spinner } from "@/components/ui";
import { get } from "@/lib/api";
import { KIND_ORDER, KIND_TONE, type PluginSpec } from "@/lib/plugins";

const KIND_NOTE: Record<string, string> = {
  source: "외부·내부 자료를 레코드로 만든다. 특허·논문 API 커넥터를 붙일 자리도 여기다.",
  enrich: "레코드에서 정해진 항목을 뽑고 분류체계에 붙인다.",
  score: "스코어카드의 차원과 가중치로 채점하고 근거를 남긴다.",
  match: "레코드끼리 의미 기반으로 연결하고 사람이 승인한다.",
  export: "결과를 문서로 만들어 저장소에 되돌려 넣는다.",
};

/** 관리자용 플러그인 카탈로그. 실제 배선은 저장소별 [파이프라인] 탭에서 한다 —
 *  같은 플러그인이 저장소마다 다른 설정으로 붙기 때문. */
export function PluginsPanel() {
  const [specs, setSpecs] = useState<PluginSpec[] | null>(null);
  useEffect(() => { get<{ items: PluginSpec[] }>("/api/plugins").then((d) => setSpecs(d.items)).catch(() => setSpecs([])); }, []);
  if (!specs) return <Spinner />;

  return (
    <div className="space-y-5">
      <Card className="px-4 py-3">
        <p className="text-[13px] font-semibold">파이프라인 플러그인</p>
        <p className="mt-1 text-[12.5px] leading-relaxed text-[#8b949e]">
          RAG 저장소 위에 얹는 업무 단계입니다. 여기는 <b>무엇이 설치되어 있는지</b>를 보는 화면이고,
          실제 배선과 실행은 각 저장소의 [파이프라인] 탭에서 합니다 — 같은 플러그인이라도 저장소마다
          다른 설정으로 붙기 때문입니다.
        </p>
        <p className="mt-2 rounded-lg bg-muted px-3 py-2 text-[12px] leading-relaxed text-[#57606a]">
          새 플러그인은 <code className="text-[11px]">backend/src/ragb/plugins/</code> 에 파일 하나를 추가하고
          <code className="mx-1 text-[11px]">register(...)</code> 를 호출하면 됩니다. 다른 곳은 손대지 않습니다 —
          설정 폼은 플러그인이 선언한 필드로 자동 생성됩니다.
        </p>
      </Card>

      {KIND_ORDER.map((kind) => {
        const group = specs.filter((s) => s.kind === kind);
        if (!group.length) return null;
        return (
          <div key={kind}>
            <div className="mb-1.5 flex items-center gap-2">
              <Badge tone={KIND_TONE[kind]}>{group[0].kind_label}</Badge>
              <span className="text-[12px] text-[#8b949e]">{KIND_NOTE[kind]}</span>
            </div>
            <Card className="divide-y divide-line">
              {group.map((s) => (
                <div key={s.id} className="px-4 py-3">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-medium">{s.name}</span>
                    <code className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-[#57606a]">{s.id}</code>
                    {s.needs_llm && <Badge tone="warn">모델 필요</Badge>}
                    {s.needs_embedding && <Badge>임베딩 사용</Badge>}
                  </p>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-[#57606a]">{s.description}</p>
                  {s.produces && <p className="mt-1 text-[12px] text-[#8b949e]">→ {s.produces}</p>}
                  <p className="mt-1.5 flex flex-wrap gap-1">
                    {s.fields.map((f) => (
                      <span key={f.key} className="rounded border border-line px-1.5 py-0.5 text-[11px] text-[#8b949e]">
                        {f.key}
                        <span className="ml-1 text-[#c1c7cd]">{f.type}</span>
                        {f.required && <span className="ml-0.5 text-red-400">*</span>}
                      </span>
                    ))}
                  </p>
                </div>
              ))}
            </Card>
          </div>
        );
      })}
    </div>
  );
}
