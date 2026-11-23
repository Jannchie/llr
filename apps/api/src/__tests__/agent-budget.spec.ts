import { describe, expect, it } from "vitest";

import { createBudget, DEFAULT_LIMITS, limitsFromEnv } from "../agent-budget.js";

describe("createBudget", () => {
  it("stops at the call that reaches the ceiling on calls", () => {
    const budget = createBudget({ maxCalls: 3, maxCost: 0 });
    expect(budget.record(0.01)).toBeNull();
    expect(budget.record(0.01)).toBeNull();
    expect(budget.record(0.01)).toBe("calls");
    expect(budget.calls).toBe(3);
  });

  it("stops when the calls so far have cost the ceiling", () => {
    const budget = createBudget({ maxCalls: 0, maxCost: 0.5 });
    expect(budget.record(0.2)).toBeNull();
    expect(budget.record(0.2)).toBeNull();
    expect(budget.record(0.2)).toBe("cost");
    expect(budget.cost).toBeCloseTo(0.6);
  });

  it("counts a call whose cost the provider did not report as a call, at no cost", () => {
    const budget = createBudget({ maxCalls: 2, maxCost: 1 });
    expect(budget.record(undefined)).toBeNull();
    expect(budget.record(Number.NaN)).toBe("calls");
    expect(budget.cost).toBe(0);
  });

  it("has no ceiling where the limit is zero", () => {
    const budget = createBudget({ maxCalls: 0, maxCost: 0 });
    for (let i = 0; i < 500; i++) expect(budget.record(10)).toBeNull();
  });
});

describe("limitsFromEnv", () => {
  it("defaults when nothing is set", () => {
    expect(limitsFromEnv({})).toEqual(DEFAULT_LIMITS);
  });

  it("reads both limits, and zero as no limit", () => {
    expect(limitsFromEnv({ LLR_AGENT_MAX_CALLS: "12", LLR_AGENT_MAX_COST: "0.75" })).toEqual({ maxCalls: 12, maxCost: 0.75 });
    expect(limitsFromEnv({ LLR_AGENT_MAX_CALLS: "0", LLR_AGENT_MAX_COST: "0" })).toEqual({ maxCalls: 0, maxCost: 0 });
  });

  it("keeps the default for a value that is not a usable number", () => {
    expect(limitsFromEnv({ LLR_AGENT_MAX_CALLS: "many", LLR_AGENT_MAX_COST: "-1" })).toEqual(DEFAULT_LIMITS);
    expect(limitsFromEnv({ LLR_AGENT_MAX_CALLS: " ", LLR_AGENT_MAX_COST: "" })).toEqual(DEFAULT_LIMITS);
  });
});
