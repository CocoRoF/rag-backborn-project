export interface Repository {
  id: string; name: string; slug: string; description: string; visibility: "private" | "shared";
  settings: Record<string, unknown>; file_count: number; chunk_count: number; bytes_total: number;
  owner_id: string; can_write: boolean; created_at: string | null;
}

export interface StorageNode {
  id: string; parent_id: string | null; kind: "folder" | "file"; name: string; path: string; depth: number;
  mime: string; size_bytes: number; status: "pending" | "processing" | "ready" | "failed"; error: string;
  chunk_count: number; section_count: number; tokens: number; embedding_model: string; summary: string;
  indexed_at: string | null; updated_at: string | null;
}

export interface Agent {
  id: string; name: string; description: string; emoji: string; system_prompt: string;
  provider: string; model: string; temperature: number; max_tokens: number;
  retrieval_mode: "off" | "auto" | "agentic"; top_k: number; settings: Record<string, unknown>;
  enabled: boolean; repository_ids: string[]; created_at: string | null;
}

export interface Citation {
  node_id: string; name: string; path: string; repository_id: string; repository: string;
  heading: string; page: number | null; text: string; score: number; legs: string[];
}

export interface ToolEvent {
  type: "tool"; phase: "start" | "end"; name: string; input?: string; summary?: string;
  is_error?: boolean; duration_ms?: number;
}

export interface ChatMessage {
  id: string; role: "user" | "assistant"; content: string; citations: Citation[];
  tool_events: ToolEvent[]; error?: string; latency_ms?: number; created_at?: string;
  pending?: boolean;
}

export interface Conversation { id: string; agent_id: string; title: string; message_count: number; updated_at: string | null }

export interface ModelOption {
  provider: string; model_id: string; display_name: string; context_window: number;
  supports_tools: boolean; is_default: boolean;
}
