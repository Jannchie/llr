/**
 * Compile every shader the renderer builds, in a real WebGL2 context.
 *
 * The vitest suite cannot do this: there is no GL context under Node, so a
 * shader can be numerically correct, fully unit-tested, type-checked and still
 * fail to compile. That is not hypothetical — `sonyChroma` shipped calling
 * `srgbDecode` on a vec3 when only the float overload was declared, and nothing
 * in the suite noticed until the browser refused the program.
 *
 * This writes a page that compiles each shader and reports the driver's own log.
 * Run it and open the page:
 *
 *     pnpm --filter @llr/web check:shaders
 *
 * Headless works too, though Chrome on Windows writes nothing to stdout, so the
 * screenshot is the only way to read the result there:
 *
 *     chrome --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader \
 *            --virtual-time-budget=10000 --screenshot=shot.png <the file URL>
 *
 * On Linux `--dump-dom | grep failures=` works and is greppable in CI.
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  MASK_BLUR_SHADER, MASK_DOWNSAMPLE_SHADER, MASK_VERTEX_SHADER,
  PROCESS_SHADER, VERTEX_SHADER,
} from "../src/rendering/passes";

// "vs"/"fs" rather than the GL enums, which do not exist outside a context.
const SHADERS: Record<string, [string, string]> = {
  VERTEX_SHADER: ["vs", VERTEX_SHADER],
  PROCESS_SHADER: ["fs", PROCESS_SHADER],
  MASK_VERTEX_SHADER: ["vs", MASK_VERTEX_SHADER],
  MASK_DOWNSAMPLE_SHADER: ["fs", MASK_DOWNSAMPLE_SHADER],
  MASK_BLUR_SHADER: ["fs", MASK_BLUR_SHADER],
};

const page = `<meta charset="utf-8"><title>shader compile check</title>
<pre id="out" style="font:13px ui-monospace,monospace;white-space:pre-wrap"></pre>
<script>
const SHADERS = ${JSON.stringify(SHADERS)};
const gl = document.createElement("canvas").getContext("webgl2");
const out = document.getElementById("out");
let bad = 0;
if (!gl) {
  out.textContent = "NO WEBGL2 — cannot compile anything here";
} else {
  for (const [name, [kind, src]] of Object.entries(SHADERS)) {
    const sh = gl.createShader(kind === "vs" ? gl.VERTEX_SHADER : gl.FRAGMENT_SHADER);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    const ok = gl.getShaderParameter(sh, gl.COMPILE_STATUS);
    if (!ok) bad++;
    // The log carries line numbers into the *assembled* source, so print the
    // shader with them to make those numbers usable.
    const log = ok ? "" : "\\n" + gl.getShaderInfoLog(sh).trim().replace(/^/gm, "  ");
    out.textContent += (ok ? "OK   " : "FAIL ") + name + log + "\\n";
    console.log((ok ? "SHADERCHECK OK " : "SHADERCHECK FAIL ") + name + log);
  }
}
out.textContent += "\\nDONE failures=" + bad + "\\n";
console.log("SHADERCHECK DONE failures=" + bad);
</script>`;

const target = fileURLToPath(new URL("../shader-check.html", import.meta.url));
writeFileSync(target, page);
console.log(`wrote ${target}\nopen it in a browser; it prints one line per shader`);
