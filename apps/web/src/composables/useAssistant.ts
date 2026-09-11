import { ref, watch } from "vue";
import { API } from "../api";

/**
 * The chat side of the editing assistant. The agent loop runs on the API (see
 * apps/api/src/agent.ts); this streams its events, renders the transcript, and
 * executes the tools the model calls — they live here because they read and
 * write the edit state and the WebGL preview, which only the browser holds.
 */

export type ToolContent = { type: "text"; text: string } | { type: "image"; data: string; mimeType: string };
export type AssistantTool = {
  description: string;
  parameters: Record<string, unknown>;  // JSON schema (object)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  run: (args: any) => Promise<ToolContent[]> | ToolContent[];
};

export type ModelSpec = { provider: string; id: string };
export const modelKey = (m: ModelSpec): string => `${m.provider}/${m.id}`;

// The models the picker offers, editable in the assistant settings. Per browser
// (localStorage): the API only holds the keys, and which models a person wants
// on the menu is a preference of theirs, not of the server.
const DEFAULT_MODELS: ModelSpec[] = [
  { provider: "openai", id: "gpt-5.6-sol" },
  { provider: "openai", id: "gpt-5.6-luna" },
  { provider: "openai", id: "gpt-5.6-terra" },
  { provider: "openai", id: "gpt-6-astra" },
  { provider: "anthropic", id: "claude-opus-5" },
  { provider: "anthropic", id: "claude-fable-5-1" },
  { provider: "deepseek", id: "deepseek-flash" },
];
const MODELS_KEY = "llr.agent.models";
const SELECTED_KEY = "llr.agent.model";

function loadModels(): ModelSpec[] {
  try {
    const raw = JSON.parse(localStorage.getItem(MODELS_KEY) ?? "");
    if (Array.isArray(raw) && raw.every(m => typeof m?.provider === "string" && typeof m?.id === "string")) return raw;
  } catch { /* fall through to defaults */ }
  return DEFAULT_MODELS.map(m => ({ ...m }));
}

export type ChatEntry =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; error?: string }
  | { kind: "tool"; name: string; args: unknown; done: boolean; error?: string };

export function useAssistant(opts: { tools: () => Record<string, AssistantTool>; systemPrompt: () => string }) {
  const session = crypto.randomUUID();
  const entries = ref<ChatEntry[]>([]);
  const busy = ref(false);
  const models = ref<ModelSpec[]>(loadModels());
  const selected = ref(localStorage.getItem(SELECTED_KEY) ?? modelKey(models.value[0]));
  // Providers the API has a key for; the picker greys out the rest.
  const providers = ref<string[] | null>(null);
  void fetch(`${API}/agent/providers`).then(r => r.ok ? r.json() : null).then(m => { providers.value = m?.providers ?? []; }).catch(() => {});
  watch(models, list => {
    localStorage.setItem(MODELS_KEY, JSON.stringify(list));
    if (list.length && !list.some(m => modelKey(m) === selected.value)) selected.value = modelKey(list[0]);
  }, { deep: true });
  watch(selected, v => localStorage.setItem(SELECTED_KEY, v));
  const currentModel = () => models.value.find(m => modelKey(m) === selected.value) ?? models.value[0];

  async function post(action: string, body: Record<string, unknown>): Promise<Response> {
    return fetch(`${API}/agent/${action}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ session, ...body }),
    });
  }

  async function runTool(toolCallId: string, name: string, args: unknown, entry: ChatEntry & { kind: "tool" }): Promise<void> {
    const tool = opts.tools()[name];
    let body: Record<string, unknown>;
    try {
      if (!tool) throw new Error(`Unknown tool ${name}`);
      body = { toolCallId, content: await tool.run(args) };
    } catch (err) {
      entry.error = err instanceof Error ? err.message : String(err);
      body = { toolCallId, error: entry.error };
    }
    entry.done = true;
    await post("tool-result", body);
  }

  async function send(text: string): Promise<void> {
    text = text.trim();
    if (!text || busy.value) return;
    busy.value = true;
    entries.value.push({ kind: "user", text });
    const tools = Object.entries(opts.tools()).map(([name, t]) => ({ name, description: t.description, parameters: t.parameters }));
    let current: (ChatEntry & { kind: "assistant" }) | null = null;
    const toolEntries = new Map<string, ChatEntry & { kind: "tool" }>();
    const toolRuns: Promise<void>[] = [];
    try {
      const model = currentModel();
      if (!model) throw new Error("No model configured");
      const res = await post("prompt", { text, tools, systemPrompt: opts.systemPrompt(), model });
      if (!res.ok || !res.body) throw new Error((await res.json().catch(() => null))?.error ?? `${res.status} ${res.statusText}`);
      for await (const ev of sseEvents(res.body)) {
        switch (ev.type) {
          case "message_start":
            if (ev.message.role === "assistant") { current = { kind: "assistant", text: "" }; entries.value.push(current); }
            break;
          case "message_update":
            if (ev.event.type === "text_delta" && current) current.text += ev.event.delta;
            break;
          case "message_end":
            if (ev.message.role === "assistant") {
              if (current && !current.text.trim() && ev.message.stopReason !== "error") entries.value.splice(entries.value.indexOf(current), 1);
              if (ev.message.stopReason === "error" && current) current.error = ev.message.errorMessage ?? "error";
              current = null;
            }
            break;
          case "tool_execution_start": {
            const entry: ChatEntry & { kind: "tool" } = { kind: "tool", name: ev.toolName, args: ev.args, done: false };
            entries.value.push(entry);
            toolEntries.set(ev.toolCallId, entry);
            toolRuns.push(runTool(ev.toolCallId, ev.toolName, ev.args, entry));
            break;
          }
          case "tool_execution_end": {
            const entry = toolEntries.get(ev.toolCallId);
            if (entry && ev.isError && !entry.error) entry.error = String(ev.result?.content?.[0]?.text ?? "error");
            break;
          }
        }
      }
      await Promise.all(toolRuns);
    } catch (err) {
      entries.value.push({ kind: "assistant", text: "", error: err instanceof Error ? err.message : String(err) });
    } finally {
      busy.value = false;
    }
  }

  function abort(): void { void post("abort", {}); }
  function reset(): void { entries.value = []; void post("reset", {}); }

  return { entries, busy, models, selected, providers, send, abort, reset };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function* sseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<any> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
  }
}
