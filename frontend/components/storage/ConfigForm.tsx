"use client";
import { Field, Input, Select, Textarea } from "@/components/ui";
import type { Collection, ConfigFieldSpec, Scorecard } from "@/lib/plugins";
import type { StorageNode } from "@/lib/types";

/** One renderer for every plug-in's configuration.

    The widget comes from the same `ConfigField` declaration the plug-in reads at run time,
    so a form can never drift from the code behind it — which is the whole reason the field
    spec is data rather than a hand-written form per plug-in. */
export function ConfigForm({ fields, value, onChange, collections, scorecards, files }: {
  fields: ConfigFieldSpec[];
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  collections: Collection[];
  scorecards: Scorecard[];
  files: StorageNode[];
}) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  const val = (f: ConfigFieldSpec) => (value[f.key] ?? f.default ?? "") as string | number;

  return (
    <div className="space-y-3.5">
      {fields.map((f) => {
        if (f.type === "bool") {
          return (
            <label key={f.key} className="flex cursor-pointer items-start gap-2.5">
              <input type="checkbox" className="mt-0.5 size-4 accent-accent"
                checked={Boolean(value[f.key] ?? f.default)}
                onChange={(e) => set(f.key, e.target.checked)} />
              <span>
                <span className="block text-[13px] font-medium">{f.label}</span>
                {f.hint && <span className="block text-[12px] text-[#8b949e]">{f.hint}</span>}
              </span>
            </label>
          );
        }
        const label = f.label + (f.required ? " *" : "");
        if (f.type === "textarea") {
          return (
            <Field key={f.key} label={label} hint={f.hint}>
              <Textarea rows={5} className="font-mono text-[12.5px]" value={String(val(f))}
                onChange={(e) => set(f.key, e.target.value)} />
            </Field>
          );
        }
        if (f.type === "number") {
          return (
            <Field key={f.key} label={label} hint={f.hint}>
              <Input type="number" step="any" value={String(val(f))} onChange={(e) => set(f.key, e.target.value)} />
            </Field>
          );
        }
        if (f.type === "select") {
          return (
            <Field key={f.key} label={label} hint={f.hint}>
              <Select value={String(val(f))} onChange={(e) => set(f.key, e.target.value)}>
                {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            </Field>
          );
        }
        if (f.type === "collection") {
          return (
            <Field key={f.key} label={label} hint={f.hint}>
              <Select value={String(val(f))} onChange={(e) => set(f.key, e.target.value)}>
                <option value="">선택하세요</option>
                {collections.map((c) => (
                  <option key={c.key} value={c.key}>{c.name} ({c.key}) · {c.record_count}건</option>
                ))}
              </Select>
            </Field>
          );
        }
        if (f.type === "scorecard") {
          return (
            <Field key={f.key} label={label} hint={f.hint}>
              <Select value={String(val(f))} onChange={(e) => set(f.key, e.target.value)}>
                <option value="">기본 스코어카드</option>
                {scorecards.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </Select>
            </Field>
          );
        }
        if (f.type === "node") {
          // Free text, because a path may point at something not uploaded yet — the list is a
          // convenience, not a constraint.
          return (
            <Field key={f.key} label={label} hint={f.hint}>
              <Input list={`nodes-${f.key}`} value={String(val(f))} placeholder="/폴더/파일.csv"
                onChange={(e) => set(f.key, e.target.value)} />
              <datalist id={`nodes-${f.key}`}>
                {files.map((n) => <option key={n.id} value={n.path} />)}
              </datalist>
            </Field>
          );
        }
        return (
          <Field key={f.key} label={label} hint={f.hint}>
            <Input value={String(val(f))} onChange={(e) => set(f.key, e.target.value)} />
          </Field>
        );
      })}
    </div>
  );
}
