// The editing assistant: a pi-agent-core Agent per browser session, hosted here
// because pi-ai's provider SDKs do not bundle for the browser. The API holds the
// LLM key and runs the loop; every tool the model calls is executed by the
// browser, which owns the edit state and the WebGL preview. The browser sends
// the tool schemas with each prompt and learns of a call through the streamed
// `tool_execution_start` event; its answer arrives over POST /agent/tool-result
// and settles the promise the loop is waiting on.
import type { ServerResponse } from "node:http";

import { Agent, type AgentEvent, type AgentMessage, type AgentTool, type AgentToolResult } from "@earendil-works/pi-agent-core";
import { clampThinkingLevel, type Api, type ImageContent, type Model, type ModelThinkingLevel, type TextContent, type TSchema } from "@earendil-works/pi-ai";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";

// Every built-in provider, its model catalog and its auth (the provider's
// usual env var: ANTHROPIC_API_KEY, OPENAI_API_KEY, …). The catalog knows each
// model's request surface — Claude's adaptive-only thinking, effort levels,
// which parameters a model rejects — so requests need no patching here.
const models = builtinModels();

// `role` is all the API knows about a tool: whether it changes the photo or
// looks at it, so the nudge below can tell "edited and never looked" without
// knowing any tool by name.
export type ToolSpec = { name: string; description: string; parameters: Record<string, unknown>; role?: "mutate" | "inspect" };
export type ModelSpec = { provider: string; id: string };
export type PromptBody = {
  session: string;
  text: string;
  systemPrompt: string;
  tools: ToolSpec[];
  model: ModelSpec;
  // Clamped to what the model supports: a non-reasoning model runs at "off".
  thinking?: ModelThinkingLevel;
  images?: ImageContent[];
};
export type ToolResultBody = {
  session: string;
  toolCallId: string;
  content?: (TextContent | ImageContent)[];
  error?: string;
};

type Pending = { resolve: (r: AgentToolResult<undefined>) => void; reject: (e: Error) => void };
// `prompt` is the system prompt the transcript holds: the transcript owns it, so
// a different one later is appended as a system message, never swapped in place.
type Session = { agent: Agent; pending: Map<string, Pending>; lastUsed: number; prompt: string };

const SESSION_IDLE_MS = 60 * 60 * 1000;
const sessions = new Map<string, Session>();
setInterval(() => {
  const cutoff = Date.now() - SESSION_IDLE_MS;
  for (const [id, s] of sessions) if (s.lastUsed < cutoff && !s.agent.state.isStreaming) sessions.delete(id);
}, 10 * 60 * 1000).unref();

// The browser names the model. Ids the catalog has not heard of yet (a model
// released after this pi-ai) are served through a sibling of the same provider
// — same endpoint and API flavour, only the id differs. Vision is assumed: every
// current frontier model takes images, and a text-only sibling would otherwise
// silently swap view_image's frames for placeholders.
export function resolveModel(spec: ModelSpec): Model<Api> {
  if (!models.getProvider(spec.provider)) throw new Error(`Unknown provider "${spec.provider}"`);
  const known = models.getModel(spec.provider, spec.id);
  if (known) return known;
  const sibling = models.getModels(spec.provider)[0];
  if (!sibling) throw new Error(`No template model for provider "${spec.provider}"`);
  return { ...sibling, id: spec.id, name: spec.id, input: ["text", "image"] };
}

/**
 * What the model dialog offers: every provider the catalog knows, the ids it
 * ships for it, and whether this server holds that provider's key. An id newer
 * than the catalog can still be typed — the browser only needs the list to
 * show what is usual.
 */
export type ProviderCatalogEntry = { id: string; hasKey: boolean; models: { id: string; name: string }[] };

export async function agentCatalog(): Promise<ProviderCatalogEntry[]> {
  return Promise.all(models.getProviders().map(async p => ({
    id: p.id,
    // A broken credential (expired OAuth) reads as "no key" rather than failing the list.
    hasKey: !!(await models.getAuth(p.id).catch(() => undefined)),
    models: models.getModels(p.id).map(m => ({ id: m.id, name: m.name })),
  })));
}

function getSession(id: string, init: { model: Model<Api>; systemPrompt: string }): Session {
  let s = sessions.get(id);
  if (!s) {
    s = {
      agent: new Agent({
        initialState: { model: init.model, systemPrompt: init.systemPrompt },
        streamFn: models.streamSimple.bind(models),
        sessionId: id,
      }),
      pending: new Map(), lastUsed: Date.now(), prompt: init.systemPrompt,
    };
    sessions.set(id, s);
  }
  s.lastUsed = Date.now();
  return s;
}

// Each tool's execute() hands the call to the browser and waits for its answer.
function browserTools(s: Session, specs: ToolSpec[]): AgentTool[] {
  return specs.map(spec => ({
    name: spec.name,
    label: spec.name,
    description: spec.description,
    parameters: spec.parameters as unknown as TSchema,
    execute: (toolCallId, _params, signal) => new Promise<AgentToolResult<undefined>>((resolve, reject) => {
      s.pending.set(toolCallId, { resolve, reject });
      signal?.addEventListener("abort", () => {
        if (s.pending.delete(toolCallId)) reject(new Error("aborted"));
      }, { once: true });
    }),
  }));
}

// What the browser needs of each event. `partial`/full messages are dropped
// from the stream deltas (the browser rebuilds text from the deltas), and tool
// results are not echoed back — the browser produced them. The nudge (see
// handleAgentPrompt) is flagged so the browser shows it as a status line, not
// as something the user said.
function serialize(event: AgentEvent, nudge: AgentMessage | null): unknown {
  switch (event.type) {
    case "message_update": {
      const { partial: _p, ...rest } = event.assistantMessageEvent as { partial?: unknown } & Record<string, unknown>;
      return { type: event.type, event: rest };
    }
    case "message_start":
    case "message_end":
      return event.message.role === "toolResult" ? null : event.message === nudge ? { ...event, nudge: true } : event;
    case "agent_end":
      return { type: event.type };
    case "turn_end":
      return { type: event.type, message: event.message };
    default:
      return event;
  }
}

export function isSessionBusy(session: string): boolean {
  return sessions.get(session)?.agent.state.isStreaming ?? false;
}

export async function handleAgentPrompt(body: PromptBody, response: ServerResponse): Promise<void> {
  const model = resolveModel(body.model);
  const s = getSession(body.session, { model, systemPrompt: body.systemPrompt });
  s.agent.state.model = model;
  s.agent.state.thinkingLevel = clampThinkingLevel(model, body.thinking ?? "off");
  s.agent.state.tools = browserTools(s, body.tools);
  // The prompt changed since the transcript began (the UI language, a newer
  // build): append it as instructions that supersede, keeping the history and
  // its cache — the transcript is append-only.
  const promptPatch: AgentMessage[] = [];
  if (body.systemPrompt !== s.prompt) {
    promptPatch.push({ role: "system", content: `Updated instructions (they replace the earlier ones):\n${body.systemPrompt}`, timestamp: Date.now() } as AgentMessage);
    s.prompt = body.systemPrompt;
  }
  response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive" });

  // The prompt tells the model to look at every edit before answering; this
  // is the backstop for a model that edits and declares success anyway. Once
  // per prompt: when a turn ends with no tool calls while an edit is still
  // unseen, a follow-up asks it to look. The loop awaits turn_end listeners
  // before polling the follow-up queue, so the message lands in this run.
  const roles = new Map(body.tools.map(t => [t.name, t.role]));
  let editedSinceLook = false;
  let nudge: AgentMessage | null = null;
  const unsubscribe = s.agent.subscribe(event => {
    if (event.type === "tool_execution_start") {
      const role = roles.get(event.toolName);
      if (role === "mutate") editedSinceLook = true;
      else if (role === "inspect") editedSinceLook = false;
    } else if (event.type === "turn_end" && event.message.role === "assistant" && event.message.stopReason === "stop"
      && editedSinceLook && !nudge && !event.message.content.some(c => c.type === "toolCall")) {
      nudge = { role: "user", timestamp: Date.now(), content: [{ type: "text",
        text: "You changed the edit but did not look at the result. Call compare, judge it, refine if needed, then answer." }] };
      s.agent.followUp(nudge);
    }
    const out = serialize(event, nudge);
    if (out && !response.writableEnded) response.write(`data: ${JSON.stringify(out)}\n\n`);
  });
  // The browser walked away mid-run: nothing will ever answer the pending tool
  // calls, so fail them and stop the loop instead of pinning the session.
  const onClose = () => {
    for (const p of s.pending.values()) p.reject(new Error("browser disconnected"));
    s.pending.clear();
    s.agent.abort();
  };
  response.on("close", onClose);
  try {
    if (promptPatch.length) {
      await s.agent.prompt([...promptPatch, { role: "user", content: [{ type: "text", text: body.text }, ...(body.images ?? [])], timestamp: Date.now() } as AgentMessage]);
    } else {
      await s.agent.prompt(body.text, body.images);
    }
  } finally {
    unsubscribe();
    response.off("close", onClose);
    s.lastUsed = Date.now();
    if (!response.writableEnded) response.end();
  }
}

export function handleAgentToolResult(body: ToolResultBody): boolean {
  const p = sessions.get(body.session)?.pending.get(body.toolCallId);
  if (!p) return false;
  sessions.get(body.session)!.pending.delete(body.toolCallId);
  if (body.error) p.reject(new Error(body.error));
  else p.resolve({ content: body.content ?? [], details: undefined });
  return true;
}

// A word in edgeways while the model is working: injected before its next
// LLM call, after the tool calls in flight have returned.
export function handleAgentSteer(session: string, text: string): void {
  sessions.get(session)?.agent.steer({ role: "user", content: [{ type: "text", text }], timestamp: Date.now() });
}

export function handleAgentAbort(session: string): void {
  sessions.get(session)?.agent.abort();
}

export function handleAgentReset(session: string): void {
  sessions.get(session)?.agent.reset();
}
