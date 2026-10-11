import { describe, expect, it } from "vitest";
import { agentCatalog, resolveModel } from "../agent.js";

describe("assistant model catalog", () => {
  it("offers the current Claude models from pi-ai's own catalog", async () => {
    const anthropic = (await agentCatalog()).find(p => p.id === "anthropic")!;
    const ids = anthropic.models.map(m => m.id);
    for (const id of ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"]) expect(ids).toContain(id);
  });

  it("resolves them with their real window, output cap, vision and adaptive-only thinking", () => {
    const m = resolveModel({ provider: "anthropic", id: "claude-opus-5-5" });
    expect(m).toMatchObject({ id: "claude-opus-5-5", provider: "anthropic", reasoning: true, contextWindow: 1_000_000, maxTokens: 128_000 });
    expect(m.input).toContain("image");
    expect((m as { compat?: { forceAdaptiveThinking?: boolean } }).compat?.forceAdaptiveThinking).toBe(true);
  });

  it("serves an id newer than the catalog through a sibling, and refuses an unknown provider", () => {
    const m = resolveModel({ provider: "anthropic", id: "claude-future-9" });
    expect(m).toMatchObject({ id: "claude-future-9", provider: "anthropic" });
    expect(() => resolveModel({ provider: "nope", id: "x" })).toThrow(/Unknown provider/);
  });
});
