import { describe, expect, it } from "vitest";
import { upgradeModels, type ModelSpec } from "../composables/useAssistant";

const ids = (list: ModelSpec[]) => list.map(m => m.id);

describe("saved assistant model list upgrades", () => {
  it("moves Fable 5.1 on to 5.2 in place, keeping its thinking level", () => {
    const saved: ModelSpec[] = [
      { provider: "anthropic", id: "claude-opus-5-5" },
      { provider: "anthropic", id: "claude-fable-5-1", thinking: "high" },
      { provider: "deepseek", id: "deepseek-flash" },
    ];
    expect(upgradeModels(saved, 2)).toEqual([
      { provider: "anthropic", id: "claude-opus-5-5" },
      { provider: "anthropic", id: "claude-fable-5-2", thinking: "high" },
      { provider: "deepseek", id: "deepseek-flash" },
    ]);
  });

  it("drops the old entry when the successor was already added by hand", () => {
    const saved: ModelSpec[] = [
      { provider: "anthropic", id: "claude-fable-5-1" },
      { provider: "anthropic", id: "claude-fable-5-2", thinking: "low" },
    ];
    expect(upgradeModels(saved, 2)).toEqual([{ provider: "anthropic", id: "claude-fable-5-2", thinking: "low" }]);
  });

  it("applies every step to a list from before v2", () => {
    const saved: ModelSpec[] = [
      { provider: "anthropic", id: "claude-opus-5", thinking: "medium" },
      { provider: "anthropic", id: "claude-fable-5-1" },
    ];
    expect(ids(upgradeModels(saved, 1))).toEqual(["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"]);
  });

  it("leaves a current list and other providers' ids alone", () => {
    const saved: ModelSpec[] = [{ provider: "opencode", id: "claude-fable-5-1" }, { provider: "anthropic", id: "claude-fable-5-1" }];
    expect(upgradeModels(saved, 3)).toEqual(saved);
    expect(ids(upgradeModels(saved, 2))).toEqual(["claude-fable-5-1", "claude-fable-5-2"]);
  });
});
