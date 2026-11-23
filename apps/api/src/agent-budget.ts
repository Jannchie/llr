// How far one prompt may run before the server stops it.
//
// The agent loop is the model's: it edits, looks, edits again, for as long as
// it keeps calling tools — and the browser answers every call. A model that
// settles into change-compare-change never ends on its own, each round costs
// more than the last (the screenshots accumulate in its context), and the
// only signal is the provider's bill. So a prompt has a ceiling on LLM calls
// and on what they have cost; reaching either stops the run and says so. The
// user can send "continue" for another prompt's worth.

export type BudgetLimits = {
  /** LLM calls in one prompt; 0 = no limit. */
  maxCalls: number;
  /** Dollars in one prompt, by the provider's own usage figures; 0 = no limit. */
  maxCost: number;
};

// A whole-look request on a frontier model runs around ten calls and well
// under a dollar; these leave room for a hard one and stop a runaway.
export const DEFAULT_LIMITS: BudgetLimits = { maxCalls: 40, maxCost: 2 };

/** `LLR_AGENT_MAX_CALLS` / `LLR_AGENT_MAX_COST`; anything unreadable keeps the default. */
export function limitsFromEnv(env: Record<string, string | undefined>): BudgetLimits {
  const read = (value: string | undefined, fallback: number): number => {
    if (value === undefined || value.trim() === "") return fallback;
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? n : fallback;
  };
  return {
    maxCalls: Math.floor(read(env.LLR_AGENT_MAX_CALLS, DEFAULT_LIMITS.maxCalls)),
    maxCost: read(env.LLR_AGENT_MAX_COST, DEFAULT_LIMITS.maxCost),
  };
}

export type BudgetHit = "calls" | "cost";

/** One prompt's running total. `record` takes each finished LLM call and says which limit, if any, it reached. */
export function createBudget(limits: BudgetLimits) {
  let calls = 0;
  let cost = 0;
  return {
    record(callCost: number | undefined): BudgetHit | null {
      calls += 1;
      cost += Number.isFinite(callCost) ? callCost! : 0;
      if (limits.maxCalls > 0 && calls >= limits.maxCalls) return "calls";
      if (limits.maxCost > 0 && cost >= limits.maxCost) return "cost";
      return null;
    },
    get calls() { return calls; },
    get cost() { return cost; },
  };
}
