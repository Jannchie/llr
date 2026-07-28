/**
 * Build every program the renderer builds, in a real WebGL2 context.
 *
 * The vitest suite cannot do this: there is no GL context under Node, so a
 * shader can be numerically correct, fully unit-tested, type-checked and still
 * fail to compile. That is not hypothetical — `sonyChroma` shipped calling
 * `srgbDecode` on a vec3 when only the float overload was declared, and nothing
 * in the suite noticed until the browser refused the program.
 *
 * This checks whole programs rather than lone shaders, because linking is where
 * a second class of error surfaces: a varying that one side declares and the
 * other does not compiles fine twice over and fails only at link. The pairs
 * mirror pipeline-renderer's own (compileProgram / ensureMaskPrograms).
 *
 *     pnpm --filter @llr/web check:shaders
 *
 * NOTE: that command only writes the page — nothing is compiled until a browser
 * opens it, so it exits 0 even when a shader is broken. Do not chain it into
 * the root `check` script expecting it to gate anything. To get a real verdict,
 * open the page, or drive it headless:
 *
 *     chrome --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader \
 *            --virtual-time-budget=10000 --dump-dom <file URL> | grep failures=
 *
 * On Windows Chrome writes nothing to stdout, so there `--screenshot=shot.png`
 * and reading the image is the only way to see the result.
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  MASK_BLUR_SHADER, MASK_DOWNSAMPLE_SHADER, MASK_VERTEX_SHADER,
  PROCESS_SHADER, SONY_POST_PROGRAMS, VERTEX_SHADER,
} from "../src/rendering/passes";

const PROGRAMS: [name: string, vs: string, fs: string][] = [
  ["process", VERTEX_SHADER, PROCESS_SHADER],
  ["mask downsample", MASK_VERTEX_SHADER, MASK_DOWNSAMPLE_SHADER],
  ["mask blur", MASK_VERTEX_SHADER, MASK_BLUR_SHADER],
  // Sony's post chain, paired with MASK_VERTEX_SHADER the way
  // pipeline-renderer.postProgram builds them. These were missing here, which
  // left the largest shader in the app — Spica's, with its two lookup textures
  // and three loops — with nothing checking that it compiles at all.
  ...Object.entries(SONY_POST_PROGRAMS).map(
    ([name, def]): [string, string, string] =>
      [`sony ${name}`, MASK_VERTEX_SHADER, def.fsSource]),
];

const page = `<meta charset="utf-8"><title>shader compile check</title>
<pre id="out" style="font:13px ui-monospace,monospace;white-space:pre-wrap"></pre>
<script>
const PROGRAMS = ${JSON.stringify(PROGRAMS)};
const gl = document.createElement("canvas").getContext("webgl2");
const out = document.getElementById("out");
let bad = 0;

// Compile one shader, or return the driver's log. Indented so it reads as a
// block under the program's own line; the line numbers are into the assembled
// source, which is why the whole shader is passed through untouched.
function build(type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  return gl.getShaderParameter(sh, gl.COMPILE_STATUS)
    ? sh : gl.getShaderInfoLog(sh).trim().replace(/^/gm, "  ");
}

function report(line) {
  out.textContent += line + "\\n";
  console.log("SHADERCHECK " + line);
}

if (!gl) {
  out.textContent = "NO WEBGL2 — cannot compile anything here";
} else {
  for (const [name, vsSrc, fsSrc] of PROGRAMS) {
    const vs = build(gl.VERTEX_SHADER, vsSrc);
    const fs = build(gl.FRAGMENT_SHADER, fsSrc);
    if (typeof vs === "string" || typeof fs === "string") {
      bad++;
      report("FAIL " + name + "\\n" + [vs, fs].filter(s => typeof s === "string").join("\\n"));
      continue;
    }
    const prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      report("OK   " + name);
    } else {
      bad++;
      report("LINK " + name + "\\n  " + gl.getProgramInfoLog(prog).trim());
    }
  }
}
report("DONE failures=" + bad);
</script>`;

const target = fileURLToPath(new URL("../shader-check.html", import.meta.url));
writeFileSync(target, page);
console.log(`wrote ${target}\nnothing is compiled yet — open it in a browser to get the verdict`);
