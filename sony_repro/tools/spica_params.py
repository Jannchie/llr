r"""抓 Spica 两个核心滤波器的**运行时参数** —— 不靠手读汇编推。

静态已经读出形状(见 PIPELINE §7.11.4):

* `FUN_140359390` 分类器: 5x5 十字取 9 点求 min/max,阈值 min+(max-min)/2,
  出 9 位 LBP 掩码;bit8 置位则取反并只留低 8 位 => 0..255 的表索引。
  平坦区(max-min < 某 float 阈值)直接返回 0。那个阈值在第 7 参数的 `+8`。
* `FUN_1403599a0` 方向滤波: 25 个 float 权重(`w[+4]` .. `w[+0x64]`),采样网格
  沿运行时传入的 (dx, dy) 拉伸 —— 一族方向可调的核。

**25 个 tap 谁对谁不手推**:`mulss` 吃的是上一轮 `movzx` 的值,长直线代码里
流水线错位极易读反。这里只抓「权重数组是什么、dx/dy 取哪些值、调几次」这些
事实,tap 的几何留给数值反解。

> ⚠️ **上面那两个标量函数一次都不会被调用** —— 挂上去零命中,静态扫
> `ZcTaskSIMDSpica` 的 call 目标也是直接调用 0 次。它们只能用来理解算法。
> 执行路径上跑的是 SIMD 版自己的两个内部函数,**各调 2 次**(对上「分两遍」):
>
> * `0x39ead0` —— 主力,**每像素调用**。上来就 `r10d = dx + 2*dy`,然后 switch
>   `{-3, -1, 1, 3}`:**方向被硬编码成 4 个分支**,不是标量版那种任意 (dx,dy)。
> * `0x39e740` —— **运行时零命中**。两处调用点都紧跟 `0x39ead0`,且都被条件跳转
>   守卫。角色未定,别当成分类器。
>
> 这和 Sharpness 相反:那边标量孪生既可读又在跑,这边可读的那份是死代码。

**签名还没定死**,所以这里不猜参数含义,只抓 `args[0..15]` 的原始快照 —— 哪几个
是坐标、哪个是权重指针,靠取值分布反推(指针高位固定、坐标随像素递增)。

用法(Windows 的 python):
    python spica_params.py <ARW> [--hits 400] [--secs 60]
"""
import collections
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
CLASSIFY, FILTER = 0x39E740, 0x39EAD0     # SIMD 版的,不是标量那两个(见模块头)
NARG = 16


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const HITS = HITSJS, NARG = NARGJS;
let n = {classify: 0, filter: 0};

// 签名未知,所以只记原始快照:每个参数同时按「整数」和「指针指向的 32 个 float」
// 两种解释存下来。指针的高位固定、坐标随像素递增,靠取值分布就能分辨。
function snap(a) {
  const out = [];
  for (let i = 0; i < NARG; i++) {
    const v = a[i];
    let f = null;
    try { f = Array.from({length: 32}, (_, k) => v.add(k * 4).readFloat()); } catch (e) {}
    out.push({raw: v.toString(), i32: v.toInt32(), f: f});
  }
  return out;
}

for (const [k, rva] of [['classify', CLASSIFYJS], ['filter', FILTERJS]]) {
  Interceptor.attach(base.add(rva), {
    onEnter(a) { if (n[k]++ < HITS) send({k: k, seq: n[k], args: snap(a)}); }
  });
}
send({info: 'armed'});
"""


def main():
    arw = sys.argv[1]
    hits = int(_opt("--hits", "400"))
    secs = float(_opt("--secs", "60"))
    js = (JS.replace("CLASSIFYJS", str(CLASSIFY)).replace("FILTERJS", str(FILTER))
            .replace("HITSJS", str(hits)).replace("NARGJS", str(NARG)))

    rows = []

    def on_message(msg, _data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        rows.append(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and len(rows) < 2 * hits:
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    for k in ("classify", "filter"):
        rs = [r for r in rows if r["k"] == k]
        print(f"\n=== {k}  {len(rs)} 次 ===")
        if not rs:
            continue
        # 每个参数位置取值有几种:一种 = 常量(指针/配置),多种 = 随像素变(坐标)。
        # 这是在不知道签名的情况下分辨参数角色最省事的办法。
        for i in range(NARG):
            vals = collections.Counter(r["args"][i]["raw"] for r in rs)
            kind = "常量" if len(vals) == 1 else f"{len(vals)} 种"
            head = list(vals)[:3]
            print(f"  arg[{i:2d}]  {kind:>6}   {head}")
        # 能解引用出 float、且取值种类不多的参数,最可能是表 —— 要么一张常量表,
        # 要么一族按类别选的表(权重表族正是后者)。种类多的是坐标/缓冲,跳过。
        # 注意 readFloat 读出 NaN 时 send 会把它序列化成 null,别直接 round。
        for i in range(NARG):
            tables, f0 = {}, rs[0]["args"][i]["f"]
            if not f0 or not any(v is not None and v != 0.0 for v in f0):
                continue
            for r in rs:
                if r["args"][i]["f"]:
                    tables.setdefault(r["args"][i]["raw"], r["args"][i]["f"])
            if len(tables) > 24:
                continue
            print(f"  arg[{i}] 指向 {len(tables)} 张表,前 3 张:")
            for ptr, g in list(tables.items())[:3]:
                print(f"    {ptr}  {[None if v is None else round(v, 6) for v in g]}")

    out = os.path.join(SCR, "spica_params.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    print("SAVED", out)


if __name__ == "__main__":
    main()
