r"""ghidra:按 RVA 列表 + BFS 深度把任意一组函数反编译成 C。

`pyg_scalar.py` 是从 vftable 符号出发做 BFS 的,一次会拖出上百个函数、几百 KB,
读起来反而费劲。这个脚本直接吃 RVA,深度自己定,产物按阶段分文件。
`sharpness_decomp.c` / `vatr_decomp.c` / `rawnr_decomp.c` / `itp_decomp.c` /
`spica_decomp.c` / `chroma_suppres_decomp.c` 都是它出的。

Ghidra 与 JDK 的位置按这个顺序找:
  1. 环境变量 GHIDRA_INSTALL_DIR / JAVA_HOME
  2. tools/ghidra_12.1.2_PUBLIC 与 tools/jdk-21.0.11+10
  3. 会话 scratchpad(逆向时装在那儿,600 MB,不入库)
项目库(pygproj)也一样 —— 分析一次 5.7 MB 的 PE 要十几分钟,别删。

用法(**必须用 Windows 的 Python**):
    python pyg_stage2.py out.c 深度 最多几个函数 0x388c00 0x386da0 ...

配套:
    dump_dat.py   把反编译里的 DAT_ 常量解成 float/double/int
    dump_vtab.py  按字节偏移列 vtable(反编译里的 *this + 0x110 要能查到函数)
"""
import os
import sys

SCR = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    (os.environ.get('GHIDRA_INSTALL_DIR'), os.environ.get('JAVA_HOME')),
    (os.path.join(SCR, 'ghidra_12.1.2_PUBLIC'), os.path.join(SCR, 'jdk-21.0.11+10')),
]
_pad = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp', 'claude')
if os.path.isdir(_pad):
    for _sess in os.listdir(_pad):
        _s = os.path.join(_pad, _sess)
        if not os.path.isdir(_s):
            continue
        for _sub in os.listdir(_s):
            _p = os.path.join(_s, _sub, 'scratchpad')
            CANDIDATES.append((os.path.join(_p, 'ghidra_12.1.2_PUBLIC'),
                               os.path.join(_p, 'jdk-21.0.11+10')))

GHIDRA = JDK = None
for g, j in CANDIDATES:
    if g and j and os.path.isdir(g) and os.path.isfile(os.path.join(j, 'bin', 'java.exe')):
        GHIDRA, JDK = g, j
        break
if GHIDRA is None:
    raise SystemExit('找不到 Ghidra 12.1.2 + JDK 21,设 GHIDRA_INSTALL_DIR / JAVA_HOME')

os.environ['GHIDRA_INSTALL_DIR'] = GHIDRA
os.environ['JAVA_HOME'] = JDK
os.environ['PATH'] = os.path.join(JDK, 'bin') + os.pathsep + os.environ['PATH']
# 项目库放在 Ghidra 安装目录旁边,和它一起留在 scratchpad 里
PROJ = os.path.join(os.path.dirname(GHIDRA), 'pygproj')

import pyghidra  # noqa: E402

pyghidra.start()
BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
IB = 0x140000000

if len(sys.argv) < 5:
    raise SystemExit(__doc__)
out_path = sys.argv[1]
DEPTH = int(sys.argv[2])
MAXF = int(sys.argv[3])
ROOTS = [int(x, 16) for x in sys.argv[4:]]

with pyghidra.open_program(BIN, project_location=PROJ, project_name='edit',
                           analyze=False) as flat:
    prog = flat.getCurrentProgram()
    from ghidra.app.decompiler import DecompInterface
    from ghidra.util.task import ConsoleTaskMonitor
    mon = ConsoleTaskMonitor()
    dec = DecompInterface()
    dec.toggleCCode(True)
    dec.openProgram(prog)
    fm = prog.getFunctionManager()
    space = prog.getAddressFactory().getDefaultAddressSpace()

    seen, order = set(), []
    frontier = [(a if a >= IB else a + IB, 0) for a in ROOTS]
    while frontier and len(order) < MAXF:
        a, d = frontier.pop(0)
        fn = fm.getFunctionContaining(space.getAddress(a))
        if fn is None:
            print('!! 0x%x 处没有函数' % a, flush=True)
            continue
        ep = fn.getEntryPoint().getOffset()
        if ep in seen:
            continue
        seen.add(ep)
        order.append((fn, d))
        if d < DEPTH:
            for c in fn.getCalledFunctions(mon):
                if c.getEntryPoint().getOffset() not in seen:
                    frontier.append((c.getEntryPoint().getOffset(), d + 1))

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('// %d functions, roots=%s depth=%d\n\n'
                % (len(order), ' '.join('0x%x' % r for r in ROOTS), DEPTH))
        for fn, d in order:
            try:
                r = dec.decompileFunction(fn, 240, mon)
                c = r.getDecompiledFunction().getC() if r.decompileCompleted() else '// FAILED\n'
            except Exception as e:  # noqa: BLE001
                c = '// EXC %s\n' % e
            f.write('// ===== depth%d %s @ 0x%x rva=0x%x size=%d =====\n'
                    % (d, fn.getName(), fn.getEntryPoint().getOffset(),
                       fn.getEntryPoint().getOffset() - IB, fn.getBody().getNumAddresses()))
            f.write(c + '\n\n')
    print('WROTE %s (%d functions)' % (out_path, len(order)), flush=True)
