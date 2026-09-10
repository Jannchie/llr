/**
 * Run Marble's four chroma passes on the engine's own tile and check them
 * against the bit-exact reference.
 *
 * shader-check.ts proves the programs compile and link; this proves they
 * compute, over the whole chain wired together. The stage is a float port of an
 * integer operator (worker sony/marble.py, verified 100% against the engine's
 * buffers), so the bar is not bit-parity — it is that every step lands where
 * the reference put it:
 *
 *   - the working-space round trip (sRGB EOTF, the /8192 matrix, x^(256/563)
 *     and back) leaves a pixel where it found it;
 *   - the 5x5 stride-2 thresholds are the engine's, in the engine's units, so
 *     the same neighbours are admitted;
 *   - the upsample phases line up. This is the one that cannot be caught by
 *     eye: a half-texel error there is a smooth, plausible-looking image that
 *     is a quarter of a low-res cell off everywhere.
 *
 * The fixture is a 192x192 crop of the engine's own Marble input captured at
 * export (marble-fixture.json, regenerate with
 * sony_repro/tools/make_marble_fixture.py). Real data on purpose: noise, colour
 * edges, saturated blocks and luma texture at once. A 32-px border is excluded
 * from the compare — that covers the filter's support (16 px for the mean, plus
 * the blur and the upsample) and the edge conventions the two sides differ on
 * (the engine zero-pads partial boxes at the far edge and leaves two rows of
 * the upsample undefined; the shader clamps to the edge texel).
 *
 *     pnpm --filter @llr/web check:marble
 *
 * Writes a page and, when a Chrome is findable, opens it headless and reports
 * the verdict; without one it prints the command to run by hand. Headless needs
 * SwiftShader, since there is no GPU behind a CI box or a WSL shell:
 *
 *     chrome --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader \
 *            --user-data-dir=<tmp> --dump-dom <file URL>
 *
 * NOTE: do NOT pass --virtual-time-budget. With it the dump is never written
 * and the run looks like a crash.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  MARBLE_BLUR_SHADER,
  MARBLE_COMPOSE_SHADER,
  MARBLE_DOWN_SHADER,
  MARBLE_MEAN_SHADER,
  MASK_VERTEX_SHADER,
} from "../src/rendering/passes";
import { marbleUniforms } from "../src/rendering/sony-marble";
import FIXTURE from "./marble-fixture.json" with { type: "json" };

/** 14-bit white, the scale the fixture's integers are on. */
const WHITE = 16383;
/** Excluded from the compare on all four sides; see the header. */
const MARGIN = 32;
/**
 * What the port has to hold, in 8-bit units. Set just above what SwiftShader
 * measures (0.33 worst pixel, 0.10 mean) so a regression has somewhere to show;
 * anything under 3 and 0.3 would still be invisible, the operator itself moving
 * pixels by 24/255 on this tile.
 */
const MAX_TOLERANCE = 1;
const MEAN_TOLERANCE = 0.15;

const U = marbleUniforms(
  { iso: FIXTURE.iso, amountAuto: FIXTURE.amount, calib: FIXTURE.calib },
  FIXTURE.slider,
);

const page = `<meta charset="utf-8"><title>marble numeric check</title>
<pre id="out" style="font:13px ui-monospace,monospace;white-space:pre-wrap"></pre>
<script id="fixture" type="application/json">${JSON.stringify({
  input: FIXTURE.input, expected: FIXTURE.expected,
})}</script>
<script>
const VS = ${JSON.stringify(MASK_VERTEX_SHADER)};
const FS_DOWN = ${JSON.stringify(MARBLE_DOWN_SHADER)};
const FS_MEAN = ${JSON.stringify(MARBLE_MEAN_SHADER)};
const FS_BLUR = ${JSON.stringify(MARBLE_BLUR_SHADER)};
const FS_COMPOSE = ${JSON.stringify(MARBLE_COMPOSE_SHADER)};
const W = ${FIXTURE.w}, H = ${FIXTURE.h}, MARGIN = ${MARGIN}, WHITE = ${WHITE};
const MAX_TOL = ${MAX_TOLERANCE}, MEAN_TOL = ${MEAN_TOLERANCE};
const U = ${JSON.stringify(U)};
const fixtureNode = document.getElementById("fixture");
const FIX = JSON.parse(fixtureNode.textContent);
// Out of the document once it is parsed: --dump-dom prints whatever is left,
// and two megabytes of integers would drown the verdict (and overflow the
// pipe this is read back through).
fixtureNode.remove();

const out = document.getElementById("out");
let bad = 0;
function report(line) { out.textContent += line + "\\n"; console.log("MARBLECHECK " + line); }
function check(name, ok, detail) {
  if (!ok) bad++;
  report((ok ? "OK   " : "FAIL ") + name + (detail ? "  " + detail : ""));
}

const gl = document.createElement("canvas").getContext("webgl2");
// OES_texture_float_linear is not optional here: every texture below is RGBA32F
// with a LINEAR filter, and without the extension enabled that combination is an
// *incomplete* texture — every sample, texelFetch included, silently reads zero
// and the whole chain comes back black with no error anywhere.
if (!gl || !gl.getExtension("EXT_color_buffer_float") || !gl.getExtension("OES_texture_float_linear")) {
  bad++;
  report("NO WEBGL2 / no renderable, filterable float textures — cannot run");
} else {
  const build = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const link = (fs) => {
    const p = gl.createProgram();
    gl.attachShader(p, build(gl.VERTEX_SHADER, VS));
    gl.attachShader(p, build(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return p;
  };
  const pDown = link(FS_DOWN), pMean = link(FS_MEAN), pBlur = link(FS_BLUR), pCompose = link(FS_COMPOSE);

  // LINEAR everywhere: the compose reads the blurred planes through the
  // hardware's bilinear filter, which is what stands in for the engine's
  // weight table. The other passes only ever texelFetch, which ignores it.
  const tex = (w, h, data) => {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, w, h, 0, gl.RGBA, gl.FLOAT, data);
    for (const p of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER])
      gl.texParameteri(gl.TEXTURE_2D, p, gl.LINEAR);
    for (const p of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T])
      gl.texParameteri(gl.TEXTURE_2D, p, gl.CLAMP_TO_EDGE);
    return t;
  };
  const target = (w, h) => {
    const t = tex(w, h, null);
    const f = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
      throw new Error("incomplete framebuffer " + w + "x" + h);
    return { tex: t, fbo: f };
  };

  // A full-screen triangle pair, the geometry MASK_VERTEX_SHADER expects.
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

  // The renderer's own grid: ceil(W/4), so a partial block at the far edge
  // still gets a texel. (pipeline-renderer.ts cachedMarbleTargets.)
  const LW = Math.ceil(W / 4), LH = Math.ceil(H / 4);
  const tDown = target(LW, LH), tMean = target(LW, LH), tBlur = target(LW, LH);
  const tOut = target(W, H);
  const u = (p, n) => gl.getUniformLocation(p, n);

  /** src: Float32Array(W*H*4) of display RGB. Returns the same, cleaned. */
  function run(src) {
    const sTex = tex(W, H, src);
    const draw = (prog, srcTex, dst, extra) => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fbo);
      gl.useProgram(prog);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, srcTex);
      if (extra) extra();
      gl.bindVertexArray(vao);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    gl.viewport(0, 0, LW, LH);
    draw(pDown, sTex, tDown, () => gl.uniform1i(u(pDown, "u_scene"), 0));
    draw(pMean, tDown.tex, tMean, () => {
      gl.uniform1i(u(pMean, "u_input"), 0);
      gl.uniform3f(u(pMean, "u_thrY"), U.thrY[0], U.thrY[1], U.thrY[2]);
      gl.uniform3f(u(pMean, "u_thrC1"), U.thrC1[0], U.thrC1[1], U.thrC1[2]);
      gl.uniform3f(u(pMean, "u_thrC2"), U.thrC2[0], U.thrC2[1], U.thrC2[2]);
    });
    draw(pBlur, tMean.tex, tBlur, () => {
      gl.uniform1i(u(pBlur, "u_input"), 0);
      gl.uniform2f(u(pBlur, "u_centreMix"), U.centreMix[0], U.centreMix[1]);
    });

    gl.viewport(0, 0, W, H);
    draw(pCompose, sTex, tOut, () => {
      gl.uniform1i(u(pCompose, "u_scene"), 0);
      gl.uniform1i(u(pCompose, "u_blur"), 1);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, tBlur.tex);
      gl.activeTexture(gl.TEXTURE0);
      gl.uniform4f(u(pCompose, "u_protect"), U.protect[0], U.protect[1], U.protect[2], U.protect[3]);
      gl.uniform1f(u(pCompose, "u_strength"), U.strength);
      gl.uniform1f(u(pCompose, "u_amount"), U.amount);
    });

    const px = new Float32Array(W * H * 4);
    gl.readPixels(0, 0, W, H, gl.RGBA, gl.FLOAT, px);
    gl.deleteTexture(sTex);
    // No flip: texImage2D row 0 is texel y = 0 is fragment y = 0 is the first
    // row readPixels returns, so the fixture's row order survives the round
    // trip. It has to — the upsample's row phases are not symmetric in y, and
    // a flip would show up as a quarter-cell shift and nothing else.
    return px;
  }

  // ── the round trip on its own ───────────────────────────────────────────
  // amount 0 means the compose puts the pixel's own chroma back, so all that
  // is left is gamut_fwd -> YCC -> YCC -> gamut_inv. If that does not come
  // back where it started, nothing below means anything.
  {
    const src = new Float32Array(W * H * 4);
    for (let i = 0; i < W * H; i++) {
      src[i * 4] = FIX.input[i * 3] / WHITE;
      src[i * 4 + 1] = FIX.input[i * 3 + 1] / WHITE;
      src[i * 4 + 2] = FIX.input[i * 3 + 2] / WHITE;
      src[i * 4 + 3] = 1;
    }
    const keep = U.amount;
    U.amount = 0;
    const got = run(src);
    U.amount = keep;
    let worst = 0;
    for (let y = MARGIN; y < H - MARGIN; y++) for (let x = MARGIN; x < W - MARGIN; x++)
      for (let k = 0; k < 3; k++)
        worst = Math.max(worst, Math.abs(got[(y * W + x) * 4 + k] * WHITE - FIX.input[(y * W + x) * 3 + k]));
    check("the working-space round trip is an identity at amount 0",
          worst * 255 / WHITE < 1, "max = " + (worst * 255 / WHITE).toFixed(3) + "/255");
  }

  // ── the stage against the reference ─────────────────────────────────────
  {
    const src = new Float32Array(W * H * 4);
    for (let i = 0; i < W * H; i++) {
      src[i * 4] = FIX.input[i * 3] / WHITE;
      src[i * 4 + 1] = FIX.input[i * 3 + 1] / WHITE;
      src[i * 4 + 2] = FIX.input[i * 3 + 2] / WHITE;
      src[i * 4 + 3] = 1;
    }
    const got = run(src);
    let worst = 0, sum = 0, n = 0, moved = 0;
    for (let y = MARGIN; y < H - MARGIN; y++) {
      for (let x = MARGIN; x < W - MARGIN; x++) {
        for (let k = 0; k < 3; k++) {
          const want = FIX.expected[(y * W + x) * 3 + k];
          const d = Math.abs(got[(y * W + x) * 4 + k] * WHITE - want) * 255 / WHITE;
          worst = Math.max(worst, d); sum += d; n++;
          moved = Math.max(moved, Math.abs(want - FIX.input[(y * W + x) * 3 + k]) * 255 / WHITE);
        }
      }
    }
    const mean = sum / n;
    check("matches worker sony/marble.py on the engine's own tile",
          worst <= MAX_TOL && mean <= MEAN_TOL,
          "max = " + worst.toFixed(2) + "/255 (limit " + MAX_TOL + "), mean = " +
          mean.toFixed(4) + "/255 (limit " + MEAN_TOL + ")" +
          " — the stage itself moves up to " + moved.toFixed(1) + "/255");
  }
}
report("DONE failures=" + bad);
</script>`;

const path = fileURLToPath(new URL("../marble-check.html", import.meta.url));
writeFileSync(path, page);
console.log(`wrote ${path}`);

/** Where a Chrome might be, in the order worth trying. $CHROME wins. */
function findChrome(): string | null {
  const candidates = [
    process.env.CHROME,
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
  ].filter((c): c is string => !!c);
  return candidates.find(c => existsSync(c)) ?? null;
}

const chrome = findChrome();
if (!chrome) {
  console.log("no Chrome found — set $CHROME, or open the page by hand for the verdict");
  process.exit(0);
}

// --dump-dom prints the document once loading is done. The page's script runs
// synchronously, so by then <pre> already holds the verdict; a screenshot would
// work too but has to be read back out of a PNG.
const profile = mkdtempSync(join(tmpdir(), "marble-check-"));
let dom: string;
try {
  dom = execFileSync(chrome, [
    "--headless=new", "--disable-gpu-sandbox", "--no-sandbox",
    "--enable-unsafe-swiftshader", "--use-angle=swiftshader",
    `--user-data-dir=${profile}`, "--dump-dom", `file://${path}`,
  ], {
    encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
    timeout: 300_000, maxBuffer: 64 << 20,
  });
}
catch (err) {
  console.error(`could not run ${chrome}: ${(err as Error).message}`);
  process.exit(1);
}
finally {
  rmSync(profile, { recursive: true, force: true });
}

const body = /<pre[^>]*>([\s\S]*?)<\/pre>/.exec(dom)?.[1] ?? "";
const lines = body.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();
console.log(lines || "(the page produced no output — did it throw? run it by hand)");
const failures = /DONE failures=(\d+)/.exec(lines);
if (!failures || failures[1] !== "0") process.exit(1);
