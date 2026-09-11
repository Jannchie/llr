// The editing assistant: a pi-agent-core Agent per browser session, hosted here
// because pi-ai's provider SDKs do not bundle for the browser. The API holds the
// LLM key and runs the loop; every tool the model calls is executed by the
// browser, which owns the edit state and the WebGL preview. The browser sends
// the tool schemas with each prompt and learns of a call through the streamed
// `tool_execution_start` event; its answer arrives over POST /agent/tool-result
// and settles the promise the loop is waiting on.
import type { ServerResponse } from "node:http";

import { Agent, type AgentEvent, type AgentTool, type AgentToolResult } from "@mariozechner/pi-agent-core";
import { getEnvApiKey, getModel, getModels, getProviders, type Api, type ImageContent, type KnownProvider, type Model, type TextContent, type TSchema } from "@mariozechner/pi-ai";

export type ToolSpec = { name: string; description: string; parameters: Record<string, unknown> };
export type ModelSpec = { provider: string; id: string };
export type PromptBody = {
  session: string;
  text: string;
  systemPrompt: string;
  tools: ToolSpec[];
  model: ModelSpec;
  images?: ImageContent[];
};
export type ToolResultBody = {
  session: string;
  toolCallId: string;
  content?: (TextContent | ImageContent)[];
  error?: string;
};

type Pending = { resolve: (r: AgentToolResult<undefined>) => void; reject: (e: Error) => void };
type Session = { agent: Agent; pending: Map<string, Pending>; lastUsed: number };

const SESSION_IDLE_MS = 60 * 60 * 1000;
const sessions = new Map<string, Session>();
setInterval(() => {
  const cutoff = Date.now() - SESSION_IDLE_MS;
  for (const [id, s] of sessions) if (s.lastUsed < cutoff && !s.agent.state.isStreaming) sessions.delete(id);
}, 10 * 60 * 1000).unref();

// The browser names the model; the key is whatever the provider's usual env var
// holds. Ids the bundled registry has not heard of yet (a model released after
// pi-ai's last update) are served through a sibling of the same provider — same
// endpoint and API flavour, only the id differs. Vision is assumed: every current
// frontier model takes images, and a text-only sibling (deepseek-v4-flash in the
// registry) would otherwise silently swap view_image's frames for placeholders.
export function resolveModel(spec: ModelSpec): Model<Api> {
  const provider = spec.provider as KnownProvider;
  if (!getProviders().includes(provider)) throw new Error(`Unknown provider "${spec.provider}"`);
  const known = getModel(provider, spec.id as never) as Model<Api> | undefined;
  if (known) return known;
  const sibling = getModels(provider)[0];
  if (!sibling) throw new Error(`No template model for provider "${spec.provider}"`);
  return { ...sibling, id: spec.id, name: spec.id, input: ["text", "image"] };
}

/** Providers whose key is present in the environment. */
export function agentProviders(): string[] {
  return getProviders().filter(p => !!getEnvApiKey(p));
}

function getSession(id: string): Session {
  let s = sessions.get(id);
  if (!s) {
    s = { agent: new Agent({ getApiKey: getEnvApiKey }), pending: new Map(), lastUsed: Date.now() };
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
// results are not echoed back — the browser produced them.
function serialize(event: AgentEvent): unknown {
  switch (event.type) {
    case "message_update": {
      const { partial: _p, ...rest } = event.assistantMessageEvent as { partial?: unknown } & Record<string, unknown>;
      return { type: event.type, event: rest };
    }
    case "message_start":
    case "message_end":
      return event.message.role === "toolResult" ? null : event;
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
  const s = getSession(body.session);
  s.agent.state.model = resolveModel(body.model);
  s.agent.state.systemPrompt = body.systemPrompt;
  s.agent.state.tools = browserTools(s, body.tools);
  response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive" });

  const unsubscribe = s.agent.subscribe(event => {
    const out = serialize(event);
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
    await s.agent.prompt(body.text, body.images);
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

export function handleAgentAbort(session: string): void {
  sessions.get(session)?.agent.abort();
}

export function handleAgentReset(session: string): void {
  sessions.get(session)?.agent.reset();
}
