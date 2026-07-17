import { describe, expect, it } from "vitest";
import { PASSES, PROCESS_SHADER, VERTEX_SHADER } from "../passes";

// setUniforms silently skips any name missing from PASSES[0].uniforms (the
// location lookup returns null), so a shader uniform that is declared but not
// registered is never set — with no error anywhere. Keep the two lists in
// lock-step here instead.
describe("PROCESS_SHADER uniform registry", () => {
  // Samplers bound once at draw time, fetched by name outside the registry.
  const SAMPLERS = new Set(["u_input", "u_curve_lut", "u_profile_lut", "u_mask_lum"]);

  const declared = new Set<string>();
  // u_texXform lives in the vertex shader; scan both stages of the program.
  for (const m of (PROCESS_SHADER + VERTEX_SHADER).matchAll(/^uniform\s+\w+\s+(\w+)(?:\[(\d+)\])?;/gm)) {
    const [, name, count] = m;
    if (count) {
      for (let i = 0; i < Number(count); i++) declared.add(`${name}[${i}]`);
    } else {
      declared.add(name);
    }
  }
  const registered = new Set(PASSES[0].uniforms);

  it("declares at least the known uniform count (regex sanity)", () => {
    expect(declared.size).toBeGreaterThan(20);
  });

  it("every declared uniform is registered (or a known sampler)", () => {
    const missing = [...declared].filter(n => !registered.has(n) && !SAMPLERS.has(n.replace(/\[\d+\]$/, "")));
    expect(missing).toEqual([]);
  });

  it("every registered uniform is declared in the shader", () => {
    const stale = [...registered].filter(n => !declared.has(n));
    expect(stale).toEqual([]);
  });
});
