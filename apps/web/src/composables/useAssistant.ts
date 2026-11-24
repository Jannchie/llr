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

// One provider the API knows, with the ids its registry ships for it and where
// the server stands on its key (mirrors the API's own type): whether requests
// can authenticate, what with when it is not a key saved from here (an env
// var's name), the tail of the key that was, and whether it takes one at all.
export type ProviderCatalog = {
  id: string; hasKey: boolean; keySource?: string; keyHint?: string; takesKey?: boolean;
  models: { id: string; name: string }[];
};

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
  { provider: "anthropic", id: "claude-opus-5-5" },
  { provider: "anthropic", id: "claude-sonnet-5-5" },
  { provider: "anthropic", id: "claude-fable-5-2" },
  { provider: "deepseek", id: "deepseek-flash" },
];
const MODELS_KEY = "llr.agent.models";
const SELECTED_KEY = "llr.agent.model";
// Bumped when the defaults move on; a list saved under an older version gets
// that move applied once (upgradeModels), after which it is the person's again.
const MODELS_VERSION_KEY = "llr.agent.models.v";
const MODELS_VERSION = 3;

// Anthropic models a default was replaced by, and the list version that did it.
// The entry keeps its place and its thinking level; only the id moves on.
const SUCCESSORS: { v: number; from: string; to: string }[] = [
  { v: 2, from: "claude-opus-5", to: "claude-opus-5-5" },
  { v: 3, from: "claude-fable-5-1", to: "claude-fable-5-2" },
];

// v2 also put Sonnet 5.5 beside Opus 5.5 — a default a list saved before it
// was missing.
export function upgradeModels(list: ModelSpec[], from: number): ModelSpec[] {
  let out = [...list];
  const is = (m: ModelSpec, id: string) => m.provider === "anthropic" && m.id === id;
  const has = (id: string) => out.some(m => is(m, id));
  for (const s of SUCCESSORS) {
    if (from >= s.v) continue;
    // The successor was already added by hand: the old entry just goes.
    out = has(s.to) ? out.filter(m => !is(m, s.from)) : out.map(m => (is(m, s.from) ? { ...m, id: s.to } : m));
  }
  if (from < 2 && !has("claude-sonnet-5-5")) {
    const at = out.findIndex(m => is(m, "claude-opus-5-5"));
    out.splice(at >= 0 ? at + 1 : out.length, 0, { provider: "anthropic", id: "claude-sonnet-5-5" });
  }
  return out;
}

function loadModels(): ModelSpec[] {
  try {
    const raw = JSON.parse(localStorage.getItem(MODELS_KEY) ?? "");
    if (Array.isArray(raw) && raw.every(m => typeof m?.provider === "string" && typeof m?.id === "string"
      && (m.thinking === undefined || THINKING_LEVELS.includes(m.thinking)))) {
      const from = Number(localStorage.getItem(MODELS_VERSION_KEY) ?? 1);
      const list = upgradeModels(raw, from);
      if (from < MODELS_VERSION) {
        localStorage.setItem(MODELS_KEY, JSON.stringify(list));
        localStorage.setItem(MODELS_VERSION_KEY, String(MODELS_VERSION));
        // The selection follows the entry that moved on.
        const selected = localStorage.getItem(SELECTED_KEY);
        const moved = SUCCESSORS.find(s => from < s.v && selected === `anthropic/${s.from}`);
        if (moved) localStorage.setItem(SELECTED_KEY, `anthropic/${moved.to}`);
      }
      return list;
    }
  } catch { /* fall through to defaults */ }
  localStorage.setItem(MODELS_VERSION_KEY, String(MODELS_VERSION));
  return DEFAULT_MODELS.map(m => ({ ...m }));
}

export type ChatEntry =
  // `steered`: sent while the model was working, so it reached it mid-run.
  | { kind: "user"; text: string; steered?: boolean }
  // `usage`: the turn's tokens and cost so far, shown under the reply.
  // `thinking`: the model's reasoning before the text, when its thinking level
  // is on; `thinkingMs` lands once it stops and marks the block as settled.
  | { kind: "assistant"; text: string; streaming: boolean; error?: string; usage?: TurnUsage; thinking: string; thinkingMs?: number }
  // `result` is what went back to the model, kept so the log can show it.
  | { kind: "tool"; name: string; args: unknown; done: boolean; error?: string; result?: ToolContent[] }
  // A line about the run itself, not something anyone said.
  | { kind: "status"; status: "stopped" | "nudge" | "limit" };

// `cost`: what the conversation has spent so far, in dollars. `queued`: a
// message that arrived as the turn was ending and goes out as the next one.
type Chat = { session: string; entries: ChatEntry[]; busy: boolean; cost: number; queued: string | null };

function pushEntry<E extends ChatEntry>(chat: Chat, entry: E): E {
  chat.entries.push(entry);
  return chat.entries[chat.entries.length - 1] as E;
}

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
    if (!chat) { chat = reactive({ session: `${page}-${key}`, entries: [], busy: false, cost: 0, queued: null }); chats.set(key, chat); }
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
  // The API's registry: what the model dialog offers to pick from.
  const catalog = ref<ProviderCatalog[] | null>(null);
  // Fetched here, and again on request: a page that came up before the API did
  // would otherwise never learn the providers (the key rows of the model
  // settings are built from them). An answer without any leaves what is known.
  function refreshCatalog(): void {
    void fetch(`${API}/agent/models`).then(r => r.ok ? r.json() : null).then(m => { catalog.value = m?.providers ?? catalog.value ?? []; }).catch(() => {});
  }
  refreshCatalog();
  // Providers the API has a key for; the picker greys out the rest. Null until
  // the catalog lands, so nothing is greyed out before we can know.
  const providers = computed(() => catalog.value?.filter(p => p.hasKey).map(p => p.id) ?? null);
  // Save a provider's API key on the server, or forget the saved one (null).
  // The key only ever travels this way; what comes back is the catalog.
  async function setKey(provider: string, key: string | null): Promise<void> {
    const r = await fetch(`${API}/agent/keys/${encodeURIComponent(provider)}`, key === null
      ? { method: "DELETE" }
      : { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ key }) });
    const body = await r.json().catch(() => null) as { providers?: ProviderCatalog[]; error?: string } | null;
    if (!r.ok || !body?.providers) throw new Error(body?.error ?? `${r.status} ${r.statusText}`);
    catalog.value = body.providers;
  }
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
      const bubble = pushEntry(chat, { kind: "user", text, steered: true });
      // The run can end between the keystroke and the request: the server
      // then has nothing to steer (409), and a bubble marked "steered" would
      // be a message the model never saw. It becomes the next turn instead.
      const res = await post(chat.session, "steer", { text }).catch(() => null);
      if (res?.ok) return;
      chat.entries.splice(chat.entries.indexOf(bubble), 1);
      if (chat.busy) chat.queued = text; else void send(text);
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
    let thinkingStart = 0;
    try {
      const model = currentModel();
      if (!model) throw new Error("No model configured");
      const res = await post(chat.session, "prompt", { text, tools, systemPrompt: opts.systemPrompt(), model, thinking: model.thinking ?? "off" });
      if (!res.ok || !res.body) throw new Error((await res.json().catch(() => null))?.error ?? `${res.status} ${res.statusText}`);
      for await (const ev of sseEvents(res.body)) {
        switch (ev.type) {
          case "message_start":
            // Mutate the reactive proxy the list hands back, not the raw literal:
            // writes to the literal render nothing until something else redraws.
            if (ev.message.role === "assistant") reply = pushEntry(chat, { kind: "assistant", text: "", streaming: true, thinking: "" });
            // The API's own follow-up (edited without looking) — a status
            // line, not a user bubble. Other user messages are already shown.
            else if (ev.nudge) chat.entries.push({ kind: "status", status: "nudge" });
            break;
          case "message_update":
            if (!reply) break;
            if (ev.event.type === "text_delta") reply.text += ev.event.delta;
            else if (ev.event.type === "thinking_start") thinkingStart = Date.now();
            else if (ev.event.type === "thinking_delta") reply.thinking += ev.event.delta;
            else if (ev.event.type === "thinking_end") reply.thinkingMs = (reply.thinkingMs ?? 0) + Date.now() - thinkingStart;
            break;
          case "message_end":
            if (ev.message.role === "assistant") {
              const u = ev.message.usage;
              if (u) {
                usage.input += u.input; usage.output += u.output; usage.cacheRead += u.cacheRead; usage.cost += u.cost?.total ?? 0;
                chat.cost += u.cost?.total ?? 0;
              }
              if (reply?.text.trim()) reply.usage = { ...usage };
              // Thinking cut short (abort, or a provider that never closes the block) still settles.
              if (reply?.thinking && reply.thinkingMs === undefined) reply.thinkingMs = Date.now() - thinkingStart;
              if (reply && !reply.text.trim() && !reply.thinking && ev.message.stopReason !== "error") chat.entries.splice(chat.entries.indexOf(reply), 1);
              if (ev.message.stopReason === "error" && reply) reply.error = ev.message.errorMessage ?? "error";
              if (ev.message.stopReason === "aborted") chat.entries.push({ kind: "status", status: "stopped" });
              if (reply) reply.streaming = false;
              reply = null;
            }
            break;
          case "limit":
            // The server's per-prompt ceiling (agent-budget.ts); the abort that
            // follows brings its own "stopped" line.
            chat.entries.push({ kind: "status", status: "limit" });
            break;
          case "tool_execution_start": {
            const entry = pushEntry(chat, { kind: "tool", name: ev.toolName, args: ev.args, done: false });
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
      chat.entries.push({ kind: "assistant", text: "", streaming: false, thinking: "", error: err instanceof Error ? err.message : String(err) });
    } finally {
      chat.busy = false;
      const queued = chat.queued;
      chat.queued = null;
      if (queued) void send(queued);
    }
  }

  function abort(): void { current.value.queued = null; void post(current.value.session, "abort", {}); }
  function reset(): void { current.value.entries = []; current.value.cost = 0; void post(current.value.session, "reset", {}); }
  /** What this conversation has cost so far, in dollars (the providers' own figures). */
  const spent = computed(() => current.value.cost);

  return { entries, busy, models, selected, catalog, providers, spent, setKey, refreshCatalog, send, abort, reset };
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
