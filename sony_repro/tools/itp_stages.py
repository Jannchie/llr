r"""ITP 的 stage 列表 —— 一次 spawn 把它们全反汇编,按形状定性。

`itp_calls.py` 用 Stalker 拿到了完整被调集合。里面有一簇函数**每 tile 各调
一次**,深度 2,集中在 `0x3b0000..0x3b4300`:那就是 ITP 的流水线级。另有
`0x3af8d0`/`0x3afac0` 紧挨着壳,也是一次。

要找的是色度降噪那一级 —— 已经黑箱量到 ITP 把 chroma/luma 比降了约 2.0x
(notes 2.20),而尾部 pack 循环证明 ITP 内部是「基准平面 + 两个差值平面」,
色度降噪只可能作用在那两个差值平面上。

按指令形状分类,不逐条读:

  * **邻域滤波**:同一基址多个不同位移的 vmovups/vaddps —— 抽头
  * **查表**:vcvttps2dq 之后跟 gather 或逐路 extract
  * **判据**:vcmpps / vblendvps / vandnps —— sigma 一类的阈值
  * **纯逐点**:只有 vmulps/vaddps,没有位移各异的载入 —— 矩阵、增益

用法(Windows 的 Python,要 frida)::

    python itp_stages.py <ARW> [--count 600]
"""
import os
import re
import subprocess
import sys
import time
from collections import Counter

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

#: 每 tile 各跑一次的那一簇,外加壳尾巴上的两个。顺序按地址,不代表执行顺序。
STAGES = [0x3AF8D0, 0x3AFAC0,
          0x3B0370, 0x3B0F00, 0x3B1040, 0x3B11F0, 0x3B1330, 0x3B14A0,
          0x3B1830, 0x3B2300, 0x3B2920, 0x3B2B40, 0x3B2BE0, 0x3B3910,
          0x3B4230]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
RVASJS.forEach(function (rva) {
  const out = [];
  let p = base.add(rva);
  for (let i = 0; i < COUNTJS; i++) {
    let ins;
    try { ins = Instruction.parse(p); } catch (e) { break; }
    out.push([p.sub(base).toString(16), ins.mnemonic, ins.opStr]);
    p = ins.next;
    if (ins.mnemonic === 'ret' && i > 24) break;
  }
  for (let i = 0; i < out.length; i += 96) {
    send({tag: 'chunk', rva: rva, rows: out.slice(i, i + 96)});
  }
  send({tag: 'done', rva: rva, n: out.length});
});
send({tag: 'end'});
"""

#: `[reg + 0x30]` / `[reg + rax + 8]` 里的位移 —— 抽头几何就藏在这些数字里。
DISP = re.compile(r"\[([a-z0-9]+)(?:\s*\+\s*[a-z0-9]+)?\s*([+-]\s*0x[0-9a-f]+)?\]")


def classify(rows):
    mn = Counter(m for _, m, _ in rows)
    ops = " ".join(o for _, _, o in rows)
    tags = []
    # 邻域滤波:同一基址上出现多个不同位移的向量载入
    loads = [m.groups() for _, mnem, o in rows if mnem.startswith("vmov")
             and "ptr" in o for m in DISP.finditer(o)]
    bydisp = Counter()
    for reg, d in loads:
        bydisp[reg] += 1
    taps = max(bydisp.values()) if bydisp else 0
    if taps >= 5:
        tags.append(f"邻域滤波(同基址 {taps} 次载入)")
    if mn["vcmpps"] or mn["vblendvps"] or mn["vandnps"]:
        tags.append(f"有判据(vcmpps {mn['vcmpps']} vblendvps {mn['vblendvps']} "
                    f"vandnps {mn['vandnps']})")
    if mn["vcvttps2dq"] or mn["vcvttss2si"] or mn["vgatherdps"]:
        tags.append(f"查表/取整(cvtt {mn['vcvttps2dq'] + mn['vcvttss2si']} "
                    f"gather {mn['vgatherdps']})")
    if mn["vmaxps"] or mn["vminps"]:
        tags.append(f"限幅(max {mn['vmaxps']} min {mn['vminps']})")
    if mn["vfmadd213ps"] or mn["vfmadd231ps"] or mn["vmulps"] > 4:
        tags.append(f"乘加为主(mul {mn['vmulps']} fma "
                    f"{mn['vfmadd213ps'] + mn['vfmadd231ps']})")
    if mn["vsqrtps"] or mn["vrsqrtps"] or mn["vrcpps"] or mn["vdivps"]:
        tags.append("有除法/开方")
    return tags or ["(没有明显的向量形状 —— 多半是调度或串行代码)"], mn


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    count = int(sys.argv[sys.argv.index("--count") + 1] if "--count" in sys.argv else 600)
    js = (JS.replace("RVASJS", repr([hex(r) for r in STAGES]).replace("'", '"'))
            .replace("COUNTJS", str(count)))

    got, done = {}, []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if p.get("tag") == "chunk":
            got.setdefault(p["rva"], []).extend(p["rows"])
        elif p.get("tag") == "end":
            done.append(True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    try:
        session = frida.attach(pid)
        script = session.create_script(js)
        script.on("message", on_message)
        script.load()
        frida.resume(pid)
        deadline = time.time() + 60
        while time.time() < deadline and not done:
            time.sleep(0.2)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    here = os.path.dirname(os.path.abspath(__file__))
    for rva in STAGES:
        key = hex(rva)
        rows = got.get(key) or got.get(rva) or []
        if not rows:
            print(f"  {rva:#x}  读不到")
            continue
        with open(os.path.join(here, f"disasm_{rva:x}.txt"), "w",
                  encoding="utf-8") as f:
            for off, mnem, o in rows:
                f.write(f"{off:>8}  {mnem:<14} {o}\n")
        tags, mn = classify(rows)
        vec = sum(v for k, v in mn.items() if k.startswith("v"))
        print(f"\n  {rva:#x}   {len(rows):>4} 条指令,其中向量 {vec:>4}")
        for t in tags:
            print(f"      {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
