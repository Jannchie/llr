import { computed, reactive, ref, watch } from "vue";
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
  // Whether the tool changes the photo or looks at it — the API's nudge
  // ("you edited but never looked") needs only this, not the tool names.
  role?: "mutate" | "inspect";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  run: (args: any) => Promise<ToolContent[]> | ToolContent[];
};

// How long the model may think before each step. Part of the model entry, so
// it persists with the list; the API clamps it to what the model supports.
export const THINKING_LEVELS = ["off", "low", "medium", "high"] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];
export type ModelSpec = { provider: string; id: string; thinking?: ThinkingLevel };
export const modelKey = (m: ModelSpec): string => `${m.provider}/${m.id}`;

// Tokens and cost of one turn, summed over its LLM calls (pi-ai's Usage).
export type TurnUsage = { input: number; output: number; cacheRead: number; cost: number };

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
    if (Array.isArray(raw) && raw.every(m => typeof m?.provider === "string" && typeof m?.id === "string"
      && (m.thinking === undefined || THINKING_LEVELS.includes(m.thinking)))) return raw;
  } catch { /* fall through to defaults */ }
  return DEFAULT_MODELS.map(m => ({ ...m }));
}

export type ChatEntry =
  // `steered`: sent while the model was working, so it reached it mid-run.
  | { kind: "user"; text: string; steered?: boolean }
  // `usage`: the turn's tokens and cost so far, shown under the reply.
  | { kind: "assistant"; text: string; streaming: boolean; error?: string; usage?: TurnUsage }
  // `result` is what went back to the model, kept so the log can show it.
  | { kind: "tool"; name: string; args: unknown; done: boolean; error?: string; result?: ToolContent[] }
  // A line about the run itself, not something anyone said.
  | { kind: "status"; status: "stopped" | "nudge" };

type Chat = { session: string; entries: ChatEntry[]; busy: boolean };

export function useAssistant(opts: {
  tools: () => Record<string, AssistantTool>;
  systemPrompt: () => string;
  /** Which photo the chat is about; each gets its own transcript and server session. */
  scope: () => string | null;
  /** Called as a prompt goes out, before the model's first tool call. */
  onTurnStart?: () => void;
}) {
  // One conversation per photo, so advice about one shot never bleeds into the
  // next. The page prefix keeps a reload from resuming a server session whose
  // transcript this browser no longer has.
  const page = crypto.randomUUID().slice(0, 8);
  const chats = new Map<string, Chat>();
  function chatFor(scope: string | null): Chat {
    const key = scope ?? "none";
    let chat = chats.get(key);
    if (!chat) { chat = reactive({ session: `${page}-${key}`, entries: [], busy: false }); chats.set(key, chat); }
    return chat;
  }
  const current = ref<Chat>(chatFor(opts.scope()));
  watch(opts.scope, scope => {
    // A run still going belongs to the photo we are leaving; its tools would
    // otherwise land on the one we are switching to.
    if (current.value.busy) abort();
    current.value = chatFor(scope);
  });
  const entries = computed(() => current.value.entries);
  const busy = computed(() => current.value.busy);

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

  async function post(session: string, action: string, body: Record<string, unknown>): Promise<Response> {
    return fetch(`${API}/agent/${action}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ session, ...body }),
    });
  }

  async function runTool(chat: Chat, toolCallId: string, name: string, args: unknown, entry: ChatEntry & { kind: "tool" }): Promise<void> {
    const tool = opts.tools()[name];
    let body: Record<string, unknown>;
    try {
      if (!tool) throw new Error(`Unknown tool ${name}`);
      entry.result = await tool.run(args);
      body = { toolCallId, content: entry.result };
    } catch (err) {
      entry.error = err instanceof Error ? err.message : String(err);
      body = { toolCallId, error: entry.error };
    }
    entry.done = true;
    await post(chat.session, "tool-result", body);
  }

  async function send(text: string): Promise<void> {
    text = text.trim();
    const chat = current.value;
    if (!text) return;
    // Mid-run, the message is steered into the model's next LLM call rather
    // than queued behind it; the bubble goes up now, marked as such.
    if (chat.busy) {
      chat.entries.push({ kind: "user", text, steered: true });
      void post(chat.session, "steer", { text });
      return;
    }
    chat.busy = true;
    opts.onTurnStart?.();
    chat.entries.push({ kind: "user", text });
    const tools = Object.entries(opts.tools()).map(([name, t]) => ({ name, description: t.description, parameters: t.parameters, role: t.role }));
    let reply: (ChatEntry & { kind: "assistant" }) | null = null;
    const usage: TurnUsage = { input: 0, output: 0, cacheRead: 0, cost: 0 };
    const toolEntries = new Map<string, ChatEntry & { kind: "tool" }>();
    const toolRuns: Promise<void>[] = [];
    try {
      const model = currentModel();
      if (!model) throw new Error("No model configured");
      const res = await post(chat.session, "prompt", { text, tools, systemPrompt: opts.systemPrompt(), model, thinking: model.thinking ?? "off" });
      if (!res.ok || !res.body) throw new Error((await res.json().catch(() => null))?.error ?? `${res.status} ${res.statusText}`);
      for await (const ev of sseEvents(res.body)) {
        switch (ev.type) {
          case "message_start":
            if (ev.message.role === "assistant") { reply = { kind: "assistant", text: "", streaming: true }; chat.entries.push(reply); }
            // The API's own follow-up (edited without looking) — a status
            // line, not a user bubble. Other user messages are already shown.
            else if (ev.nudge) chat.entries.push({ kind: "status", status: "nudge" });
            break;
          case "message_update":
            if (ev.event.type === "text_delta" && reply) reply.text += ev.event.delta;
            break;
          case "message_end":
            if (ev.message.role === "assistant") {
              const u = ev.message.usage;
              if (u) { usage.input += u.input; usage.output += u.output; usage.cacheRead += u.cacheRead; usage.cost += u.cost?.total ?? 0; }
              if (reply?.text.trim()) reply.usage = { ...usage };
              if (reply && !reply.text.trim() && ev.message.stopReason !== "error") chat.entries.splice(chat.entries.indexOf(reply), 1);
              if (ev.message.stopReason === "error" && reply) reply.error = ev.message.errorMessage ?? "error";
              if (ev.message.stopReason === "aborted") chat.entries.push({ kind: "status", status: "stopped" });
              if (reply) reply.streaming = false;
              reply = null;
            }
            break;
          case "tool_execution_start": {
            const entry: ChatEntry & { kind: "tool" } = { kind: "tool", name: ev.toolName, args: ev.args, done: false };
            chat.entries.push(entry);
            toolEntries.set(ev.toolCallId, entry);
            toolRuns.push(runTool(chat, ev.toolCallId, ev.toolName, ev.args, entry));
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
      chat.entries.push({ kind: "assistant", text: "", streaming: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      chat.busy = false;
    }
  }

  function abort(): void { void post(current.value.session, "abort", {}); }
  function reset(): void { current.value.entries = []; void post(current.value.session, "reset", {}); }

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
