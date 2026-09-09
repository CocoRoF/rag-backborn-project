export interface ConfigFieldSpec {
  key: string; label: string; type: string; default: unknown; hint: string;
  options: { value: string; label: string }[]; required: boolean;
}

export interface PluginSpec {
  id: string; kind: "source" | "enrich" | "score" | "match" | "export";
  kind_label: string; name: string; description: string; produces: string;
  needs_llm: boolean; needs_embedding: boolean;
  fields: ConfigFieldSpec[]; defaults: Record<string, unknown>;
}

export interface Binding {
  id: string; plugin_id: string; name: string; config: Record<string, unknown>;
  enabled: boolean; sort_order: number; kind: PluginSpec["kind"]; kind_label: string;
  plugin_name: string; last_run_at: string | null; last_status: string;
}

export interface PluginRun {
  id: string; binding_id: string | null; plugin_id: string; status: string; summary: string;
  counts: Record<string, number>; error: string; log: string;
  created_at: string; finished_at: string | null;
}

export interface Collection { id: string; key: string; name: string; description: string; record_count: number }
export interface ScorecardDim { key: string; label: string; weight: number; rubric: string }
export interface Scorecard { id: string; name: string; description: string; dimensions: ScorecardDim[]; is_default: boolean }

export interface RecordScore {
  id: string; total: number; tier: "long" | "short" | "core"; status: string;
  dimensions: Record<string, { label: string; weight: number; score: number; rationale: string }>;
  evidence: { path: string; heading: string; page: number | null; score: number; quote: string }[];
}

export interface DataRecord {
  id: string; collection_id: string; external_id: string; title: string; summary: string;
  attributes: Record<string, unknown>; labels: string[]; status: string; embedded: boolean;
  source_node_id: string | null;
  evidence: { path: string; heading: string; quote: string }[];
  updated_at: string | null; score?: RecordScore;
}

export interface Link {
  id: string; from: { id: string; title: string }; to: { id: string; title: string };
  score: number; rerank_score: number | null; rationale: string; status: string; kind: string;
}

/** The five stages, in the order a pipeline runs them. */
export const KIND_ORDER: PluginSpec["kind"][] = ["source", "enrich", "score", "match", "export"];

export const KIND_TONE: Record<string, "accent" | "ok" | "warn" | "neutral"> = {
  source: "neutral", enrich: "accent", score: "warn", match: "ok", export: "neutral",
};

export const TIER_LABEL: Record<string, string> = {
  core: "핵심 Portfolio", short: "Short-list", long: "Long-list",
};
