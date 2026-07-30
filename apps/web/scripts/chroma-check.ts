/**
 * Run the chroma-cleanup chain on synthetic patterns and check what it did.
 *
 * shader-check.ts proves the programs compile and link; this proves they
 * compute, over all three passes wired together — which is where this stage's
 * own failure modes live:
 *
 *   identity   a constant image comes back unchanged. Catches a broken YCbCr
 *              round trip, which would tint the whole frame and is exactly the
 *              kind of thing a noise metric never notices.
 *   luma       luma is carried through untouched. The engine moves it 0.3% and
 *              that 0.3% belongs to Clarity, not here; if this stage touches
 *              luma it is doing something it was never measured doing.
 *   clean      chroma noise on a flat field is largely gone. The point of the
 *              stage.
 *   edge       a colour step sitting on a luma step survives. This is the check
 *              that the guidance is actually wired: an unguided version passes
 *              `clean` and still washes small saturated marks out, which is how
 *              the Python reference was caught doing it.
 *
 * Thresholds are deliberately loose. This is not a parity test against
 * worker/sony/chromanr.py — the shader is the fast form at float precision on
 * SwiftShader — it is a check that the chain does its job and none of the four
 * properties is inverted.
 *
 *     pnpm --filter @llr/web check:chroma
 *
 * Writes a page; nothing runs until a browser opens it, exactly as
 * spica-check.ts does. Headless on Windows needs its own profile and a
 * screenshot, because Chrome writes nothing to stdout there:
 *
 *     chrome --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader \
 *            --user-data-dir=<tmp> --screenshot=out.png <file URL>
 *
 * NOTE: do NOT pass --virtual-time-budget. With it the screenshot is never
 * written and the run looks like a crash.
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  CHROMA_AMOUNT,
  CHROMA_COEF_SHADER,
  CHROMA_COMPOSE_SHADER,
  CHROMA_MOMENT_SHADER,
  CHROMA_SUBSAMPLE,
  MASK_VERTEX_SHADER,
} from "../src/rendering/passes";
// A real 128x128 crop of DSC02995 and what worker/sony/chromanr.py makes of it.
// Synthetic patterns only prove nothing is inverted; real data carries noise, a
// hard colour edge, saturated blocks and luma texture at once, and agreeing on
// that is what makes the shipped shader the operator that was calibrated.
// Regenerate with the script in the same directory as this fixture's note.
import FIXTURE from "./chroma-fixture.json" with { type: "json" };

const N = 128;      // full-res patch; 16 decimated texels at 8x, enough for a 3x3 box
const MARGIN = 12;  // the border reads outside the patch — ignore it

const page = `<meta charset="utf-8"><title>chroma numeric check</title>
<pre id="out" style="font:13px ui-monospace,monospace;white-space:pre-wrap"></pre>
<script>
const VS = ${JSON.stringify(MASK_VERTEX_SHADER)};
const FS_MOMENT = ${JSON.stringify(CHROMA_MOMENT_SHADER)};
const FS_COEF = ${JSON.stringify(CHROMA_COEF_SHADER)};
const FS_COMPOSE = ${JSON.stringify(CHROMA_COMPOSE_SHADER)};
const N = ${N}, MARGIN = ${MARGIN}, S = ${CHROMA_SUBSAMPLE};
const AMT = ${CHROMA_AMOUNT};
const FIX = ${JSON.stringify(FIXTURE)};

const out = document.getElementById("out");
let bad = 0;
function report(line) { out.textContent += line + "\\n"; console.log("CHROMACHECK " + line); }
function check(name, ok, detail) {
  if (!ok) bad++;
  report((ok ? "OK   " : "FAIL ") + name + (detail ? "  " + detail : ""));
}

const gl = document.createElement("canvas").getContext("webgl2");
if (!gl || !gl.getExtension("EXT_color_buffer_float") || !gl.getExtension("OES_texture_float_linear")) {
  report("NO WEBGL2 / no float render targets or float LINEAR — cannot run");
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
  const pMoment = link(FS_MOMENT), pCoef = link(FS_COEF), pCompose = link(FS_COMPOSE);

  const tex = (w, h, data, linear) => {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, w, h, 0, gl.RGBA, gl.FLOAT, data);
    const f = linear ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f);
    for (const p of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T])
      gl.texParameteri(gl.TEXTURE_2D, p, gl.CLAMP_TO_EDGE);
    return t;
  };

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

  const fbo = gl.createFramebuffer();
  const draw = (prog, dst, w, h, setup) => {
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, dst, 0);
    gl.viewport(0, 0, w, h);
    gl.useProgram(prog);
    setup(prog);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  const bind = (prog, name, t, unit) => {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.uniform1i(gl.getUniformLocation(prog, name), unit);
  };

  const LO = Math.max(1, Math.floor(N / S));
  const m1 = tex(LO, LO, null, false);
  const m2 = tex(LO, LO, null, false);
  const coef = tex(LO, LO, null, true);   // LINEAR: this is the bilinear upsample
  const dst = tex(N, N, null, false);

  // Runs the whole chain and reads the result back as N*N RGBA floats.
  function run(pixels, amount) {
    const src = tex(N, N, pixels, true);  // LINEAR: the moment pass takes bilinear taps
    for (const [target, second] of [[m1, 0], [m2, 1]]) {
      draw(pMoment, target, LO, LO, (p) => {
        bind(p, "u_scene", src, 0);
        gl.uniform2f(gl.getUniformLocation(p, "u_sceneTexel"), 1 / N, 1 / N);
        gl.uniform1f(gl.getUniformLocation(p, "u_second"), second);
      });
    }
    draw(pCoef, coef, LO, LO, (p) => {
      bind(p, "u_m1", m1, 0);
      bind(p, "u_m2", m2, 1);
      gl.uniform2f(gl.getUniformLocation(p, "u_texel"), 1 / LO, 1 / LO);
    });
    draw(pCompose, dst, N, N, (p) => {
      bind(p, "u_scene", src, 0);
      bind(p, "u_coef", coef, 1);
      gl.uniform1f(gl.getUniformLocation(p, "u_amount"), amount);
    });
    const buf = new Float32Array(N * N * 4);
    gl.readPixels(0, 0, N, N, gl.RGBA, gl.FLOAT, buf);
    gl.deleteTexture(src);
    return buf;
  }

  const at = (b, x, y) => [b[(y * N + x) * 4], b[(y * N + x) * 4 + 1], b[(y * N + x) * 4 + 2]];
  const luma = (c) => 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
  const interior = (fn) => {
    let worst = 0;
    for (let y = MARGIN; y < N - MARGIN; y++)
      for (let x = MARGIN; x < N - MARGIN; x++) worst = Math.max(worst, fn(x, y));
    return worst;
  };

  // ---- identity: a constant image must come back unchanged ----
  {
    const px = new Float32Array(N * N * 4);
    for (let i = 0; i < N * N; i++) {
      px[i * 4] = 0.62; px[i * 4 + 1] = 0.40; px[i * 4 + 2] = 0.28; px[i * 4 + 3] = 1;
    }
    const got = run(px, 1);
    const worst = interior((x, y) => {
      const c = at(got, x, y);
      return Math.max(Math.abs(c[0] - 0.62), Math.abs(c[1] - 0.40), Math.abs(c[2] - 0.28));
    });
    check("a constant image is unchanged", worst < 2e-3, "max |delta| = " + worst.toExponential(2));
  }

  // ---- clean + luma: chroma noise on a flat field ----
  {
    let seed = 7;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff - 0.5; };
    const px = new Float32Array(N * N * 4);
    for (let i = 0; i < N * N; i++) {
      px[i * 4] = 0.45 + rnd() * 0.05;
      px[i * 4 + 1] = 0.45 + rnd() * 0.05;
      px[i * 4 + 2] = 0.45 + rnd() * 0.05;
      px[i * 4 + 3] = 1;
    }
    const got = run(px, 1);
    const spread = (get) => {
      let s = 0, n = 0;
      for (let y = MARGIN; y < N - MARGIN; y++)
        for (let x = MARGIN; x < N - MARGIN; x++) { const v = get(x, y); s += v * v; n++; }
      return Math.sqrt(s / n);
    };
    const before = spread((x, y) => {
      const i = (y * N + x) * 4; return px[i] - px[i + 1];
    });
    const after = spread((x, y) => { const c = at(got, x, y); return c[0] - c[1]; });
    check("chroma noise on a flat field is reduced", after < 0.6 * before,
          "R-G rms " + before.toExponential(2) + " -> " + after.toExponential(2));

    const dY = interior((x, y) => {
      const i = (y * N + x) * 4;
      return Math.abs(luma(at(got, x, y)) - luma([px[i], px[i + 1], px[i + 2]]));
    });
    check("luma is carried through untouched", dY < 2e-3, "max |dY| = " + dY.toExponential(2));
  }

  // ---- edge: a colour step on a luma step survives ----
  {
    const px = new Float32Array(N * N * 4);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      const i = (y * N + x) * 4;
      const right = x >= N / 2;
      px[i] = right ? 0.78 : 0.42;
      px[i + 1] = right ? 0.30 : 0.44;
      px[i + 2] = right ? 0.22 : 0.46;
      px[i + 3] = 1;
    }
    const got = run(px, 1);
    const col = (x) => {
      let s = 0;
      for (let y = MARGIN; y < N - MARGIN; y++) { const c = at(got, x, y); s += c[0] - c[1]; }
      return s / (N - 2 * MARGIN);
    };
    const want = Math.abs((0.78 - 0.30) - (0.42 - 0.44));
    const keptAt = (off) => Math.abs(col(N / 2 + off) - col(N / 2 - off)) / want;
    // Two distances, because one number cannot say whether an edge survived.
    // At +-8px the sample sits *inside* the radius-8 filter, so a transition is
    // expected and the reference itself only keeps 72% (exact) / 66.9% (fast).
    // At +-16px, outside it, both return ~100% — that is the property worth
    // asserting, and the one an unguided version fails.
    const near = keptAt(S), far = keptAt(2 * S);
    check("a colour step is intact clear of the radius", far > 0.95,
          "kept " + (far * 100).toFixed(1) + "% at +-" + (2 * S) + "px");
    check("the transition inside the radius matches the reference", near > 0.6 && near < 0.8,
          "kept " + (near * 100).toFixed(1) + "% at +-" + S +
          "px; worker chromanr.py fast s8 gives 66.9%");
  }

  // ---- amount = 0 is off ----
  {
    let seed = 11;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff - 0.5; };
    const px = new Float32Array(N * N * 4);
    for (let i = 0; i < N * N; i++) {
      px[i * 4] = 0.5 + rnd() * 0.08; px[i * 4 + 1] = 0.5 + rnd() * 0.08;
      px[i * 4 + 2] = 0.5 + rnd() * 0.08; px[i * 4 + 3] = 1;
    }
    const got = run(px, 0);
    const worst = interior((x, y) => {
      const i = (y * N + x) * 4, c = at(got, x, y);
      return Math.max(Math.abs(c[0] - px[i]), Math.abs(c[1] - px[i + 1]), Math.abs(c[2] - px[i + 2]));
    });
    check("amount = 0 leaves the frame alone", worst < 2e-3,
          "max |delta| = " + worst.toExponential(2));
  }

  // ---- parity with the worker's reference, on a real photographic crop ----
  if (FIX && FIX.n === N) {
    const px = new Float32Array(N * N * 4);
    for (let i = 0; i < N * N; i++) {
      px[i * 4] = FIX.src[i * 3];
      px[i * 4 + 1] = FIX.src[i * 3 + 1];
      px[i * 4 + 2] = FIX.src[i * 3 + 2];
      px[i * 4 + 3] = 1;
    }
    // Both ends of the blend. amount 1 pins the filter itself; AMT pins what
    // actually ships, and is the only thing that would catch the two sides
    // disagreeing about *where* the blend happens — the shader mixes in Cr/Cb
    // before rebuilding RGB, and a port that mixed afterwards would match at
    // amount 1 and drift everywhere else.
    for (const [label, amount, want] of [
      ["the filter itself (amount = 1)", 1, FIX.ref],
      ["the shipped blend (amount = " + AMT + ")", AMT, FIX.ref_shipped],
    ]) {
    if (!want) { check("fixture carries " + label, false, "regenerate it"); continue; }
    const got = run(px, amount);
    let worst = 0, sum = 0, n = 0, moved = 0;
    for (let y = MARGIN; y < N - MARGIN; y++) {
      for (let x = MARGIN; x < N - MARGIN; x++) {
        const c = at(got, x, y);
        for (let k = 0; k < 3; k++) {
          const w = want[(y * N + x) * 3 + k];
          const d = Math.abs(c[k] - w);
          worst = Math.max(worst, d); sum += d; n++;
          moved = Math.max(moved, Math.abs(w - px[(y * N + x) * 4 + k]));
        }
      }
    }
    // Loose against the reference's own excursion: this is float32 on
    // SwiftShader against float64 in numpy, over a chain with a division by a
    // variance, so bit-parity is not the bar. Agreeing to a few thousandths
    // while the operator itself moves pixels far further than that is.
    // (No backticks in here: this block lives inside a template literal.)
    // Tightened to just above what was measured (1.88e-4 mean, 3.51e-3 max), so
    // a regression has somewhere to show. The loose version this replaced passed
    // while the two sides were box-averaging and bilinear-resampling the moments
    // respectively — a real divergence it was too slack to catch.
    check("matches the worker's reference on a real crop: " + label,
          worst < 6e-3 && sum / n < 4e-4,
          "mean |delta| = " + (sum / n).toExponential(2) +
          ", max = " + worst.toExponential(2) +
          " (the filter itself moves up to " + moved.toFixed(3) + ")");
    }
  } else {
    check("chroma-fixture.json matches this patch size", false,
          "regenerate it — N is " + N);
  }
}
report("DONE failures=" + bad);
</script>`;

const path = fileURLToPath(new URL("../chroma-check.html", import.meta.url));
writeFileSync(path, page);
console.log(`wrote ${path}`);
console.log("nothing runs yet — open it in a browser to get the verdict");
