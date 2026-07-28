/**
 * Run Spica's shader on synthetic patterns and check what it did.
 *
 * shader-check.ts proves the program compiles and links; this proves it
 * computes. The two failure modes it cannot catch are exactly the ones this
 * shader is exposed to: a sign error (softening instead of sharpening, which
 * still looks like a picture) and an indexing error in the mirrored tap lookup
 * (which shows up only as a directional bias, invisible on most content).
 *
 * The vitest suite cannot host this — there is no GL context under Node — and
 * an end-to-end comparison against the engine is not the right bar either: the
 * shader is a documented float approximation of an integer operator, so it will
 * not match bit-for-bit by construction. What must hold is the behaviour:
 *
 *   flat      a patch with no detail comes through untouched. The weights sum
 *             to 512, so `S - 512*centre` is zero however the taps permute.
 *   edge      a step edge overshoots on both sides. This is the sign check:
 *             get it backwards and the stage blurs.
 *   symmetry  a left-right symmetric input gives a left-right symmetric
 *             output. This is the mirroring check — GRID[] and the (dx, dy)
 *             flip are the only things that can break it, and nothing else in
 *             the shader can hide a break.
 *
 *     pnpm --filter @llr/web check:spica
 *
 * Writes a page; nothing runs until a browser opens it, exactly as
 * shader-check.ts does. Headless, on Windows, needs its own profile and a
 * screenshot because Chrome writes nothing to stdout there:
 *
 *     chrome --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader \
 *            --user-data-dir=<tmp> --virtual-time-budget=20000 \
 *            --screenshot=out.png <file URL>
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { MASK_VERTEX_SHADER, SPICA_SHADER } from "../src/rendering/passes";
import { SPICA_CODE_COUNT, SPICA_LUT, SPICA_TABLE_COUNT, SPICA_TAP_COUNT, SPICA_WEIGHTS }
  from "../src/rendering/spica-tables";

const N = 32;                 // patch size; wide enough that the +-3 diamond stays interior
const MARGIN = 4;             // the border reads outside the patch — ignore it
const AMOUNT = 0.5;           // the camera's default blend

const page = `<meta charset="utf-8"><title>spica numeric check</title>
<pre id="out" style="font:13px ui-monospace,monospace;white-space:pre-wrap"></pre>
<script>
const VS = ${JSON.stringify(MASK_VERTEX_SHADER)};
const FS = ${JSON.stringify(SPICA_SHADER)};
const WEIGHTS = ${JSON.stringify(Array.from(SPICA_WEIGHTS))};
const LUT = ${JSON.stringify(Array.from(SPICA_LUT))};
const N = ${N}, MARGIN = ${MARGIN}, AMOUNT = ${AMOUNT};
const TAPS = ${SPICA_TAP_COUNT}, TABLES = ${SPICA_TABLE_COUNT}, CODES = ${SPICA_CODE_COUNT};

const out = document.getElementById("out");
let bad = 0;
function report(line) { out.textContent += line + "\\n"; console.log("SPICACHECK " + line); }
function check(name, ok, detail) {
  if (!ok) bad++;
  report((ok ? "OK   " : "FAIL ") + name + (detail ? "  " + detail : ""));
}

const gl = document.createElement("canvas").getContext("webgl2");
if (!gl || !gl.getExtension("EXT_color_buffer_float")) {
  report("NO WEBGL2 / no float render targets — cannot run");
} else {
  const build = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, build(gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, build(gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));

  const tex = (w, h, internal, format, data) => {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.FLOAT, data);
    for (const p of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER])
      gl.texParameteri(gl.TEXTURE_2D, p, gl.NEAREST);
    for (const p of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T])
      gl.texParameteri(gl.TEXTURE_2D, p, gl.CLAMP_TO_EDGE);
    return t;
  };

  const wTex = tex(TAPS, TABLES, gl.R32F, gl.RED, Float32Array.from(WEIGHTS));
  const lutData = new Float32Array(CODES * 4);
  for (let i = 0; i < CODES; i++) {
    lutData[i * 4] = LUT[i * 3];
    lutData[i * 4 + 1] = LUT[i * 3 + 1];
    lutData[i * 4 + 2] = LUT[i * 3 + 2];
  }
  const lTex = tex(CODES, 1, gl.RGBA32F, gl.RGBA, lutData);

  // A full-screen triangle pair, the geometry MASK_VERTEX_SHADER expects.
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

  const dst = tex(N, N, gl.RGBA32F, gl.RGBA, null);
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, dst, 0);

  /** Run the shader over a grey patch given by f(x, y) in [0, 1]; return luma out. */
  function run(f) {
    const src = new Float32Array(N * N * 4);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      const v = f(x, y), i = (y * N + x) * 4;
      src[i] = src[i + 1] = src[i + 2] = v; src[i + 3] = 1;
    }
    const sTex = tex(N, N, gl.RGBA32F, gl.RGBA, src);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, N, N);
    gl.useProgram(prog);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, sTex);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, wTex);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, lTex);
    const u = n => gl.getUniformLocation(prog, n);
    gl.uniform1i(u("u_scene"), 0); gl.uniform1i(u("u_weights"), 1); gl.uniform1i(u("u_lut"), 2);
    gl.uniform2f(u("u_sceneTexel"), 1 / N, 1 / N);
    gl.uniform1f(u("u_amount"), AMOUNT);
    gl.uniform1f(u("u_isoGain"), 1);
    gl.bindVertexArray(vao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    const px = new Float32Array(N * N * 4);
    gl.readPixels(0, 0, N, N, gl.RGBA, gl.FLOAT, px);
    gl.deleteTexture(sTex);
    // Row 0 of readPixels is the bottom of the framebuffer; f() indexes from
    // the top, so flip back or every "left/right" claim below reads mirrored.
    const o = new Float64Array(N * N);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) o[y * N + x] = px[((N - 1 - y) * N + x) * 4];
    return o;
  }

  const interior = (o, fn) => {
    for (let y = MARGIN; y < N - MARGIN; y++)
      for (let x = MARGIN; x < N - MARGIN; x++) if (!fn(o[y * N + x], x, y)) return false;
    return true;
  };

  // ── flat ────────────────────────────────────────────────────────────────
  // Nothing to sharpen: every tap sees the same value, so S = 512*centre and
  // the detail term is exactly zero however the mirroring permutes the taps.
  {
    const V = 0.5;
    const o = run(() => V);
    let worst = 0;
    interior(o, v => { worst = Math.max(worst, Math.abs(v - V)); return true; });
    check("flat patch is left alone", worst < 1e-4, "max |delta| = " + worst.toExponential(2));
  }

  // ── edge ────────────────────────────────────────────────────────────────
  // A step through the middle. Sharpening overshoots: the column just inside
  // the bright side goes above it, the one just inside the dark side below.
  // Backwards, and the two would converge instead — which is the sign bug this
  // whole file exists for.
  {
    const LOW = 0.35, HIGH = 0.65, EDGE = N / 2;
    const o = run((x) => x < EDGE ? LOW : HIGH);
    const at = (x, y) => o[y * N + x];
    const y0 = N / 2;
    const brightIn = at(EDGE, y0), darkIn = at(EDGE - 1, y0);
    check("step edge overshoots on the bright side", brightIn > HIGH + 1e-4,
          brightIn.toFixed(5) + " vs " + HIGH);
    check("step edge overshoots on the dark side", darkIn < LOW - 1e-4,
          darkIn.toFixed(5) + " vs " + LOW);
    // Far from the edge it is flat again, so nothing may happen there.
    check("far from the edge nothing moves",
          Math.abs(at(MARGIN, y0) - LOW) < 1e-4 && Math.abs(at(N - MARGIN - 1, y0) - HIGH) < 1e-4);
  }

  // ── symmetry ────────────────────────────────────────────────────────────
  // A left-right symmetric input must give a left-right symmetric output. The
  // mirrored tap lookup is the only thing in the shader that can break this,
  // and a wrong GRID[] index breaks it without breaking anything else.
  {
    const o = run((x) => {
      const d = Math.abs(x - (N - 1) / 2);
      return 0.5 + 0.2 * Math.cos(d * 1.1) * Math.exp(-d / 6);
    });
    let worst = 0;
    for (let y = MARGIN; y < N - MARGIN; y++)
      for (let x = MARGIN; x < N / 2; x++)
        worst = Math.max(worst, Math.abs(o[y * N + x] - o[y * N + (N - 1 - x)]));
    check("mirrored input gives mirrored output", worst < 1e-4,
          "max asymmetry = " + worst.toExponential(2));
  }
}
report("DONE failures=" + bad);
</script>`;

const path = fileURLToPath(new URL("../spica-check.html", import.meta.url));
writeFileSync(path, page);
console.log(`wrote ${path}`);
console.log("nothing runs yet — open it in a browser to get the verdict");
