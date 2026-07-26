r"""反编译 ITP 与 AreaComp 的**标量孪生版**。

每个 SIMD 任务在引擎里都有一个不带 SIMD 后缀的标量实现,SSCS 已经验证过两条
路径逐行对应。标量版没有 AVX intrinsic 的噪声,反编译出来是可读的 C。

ZcTaskITP        <- ZcTaskSIMDITP (0x3ae920, 4008 字节 AVX)
ZcTaskAreaComp   <- ZcTaskAreaCompSIMD (0x398a60, 5604 字节 AVX-512)
ZcTaskHueSaturation <- ZcTaskSIMDHueSaturation
ZcTaskArgoITP / ZcTaskOpenCLITP 是另外两个实现,拿来交叉印证
"""
import os

SCR = os.path.dirname(os.path.abspath(__file__))
os.environ['GHIDRA_INSTALL_DIR'] = os.path.join(SCR, 'ghidra_12.1.2_PUBLIC')
os.environ['JAVA_HOME'] = os.path.join(SCR, 'jdk-21.0.11+10')
os.environ['PATH'] = os.path.join(SCR, 'jdk-21.0.11+10', 'bin') + os.pathsep + os.environ['PATH']
import pyghidra  # noqa: E402

pyghidra.start()

BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
GROUPS = {
    
    'marble_decomp.c': ['ZcTaskMarble', 'ZcTaskSpica'],
}
MAXF, MAXD = 120, 2

with pyghidra.open_program(BIN, project_location=os.path.join(SCR, 'pygproj'),
                           project_name='edit', analyze=False) as flat:
    prog = flat.getCurrentProgram()
    from ghidra.app.decompiler import DecompInterface
    from ghidra.util.task import ConsoleTaskMonitor
    mon = ConsoleTaskMonitor()
    dec = DecompInterface()
    dec.toggleCCode(True)
    dec.openProgram(prog)
    fm = prog.getFunctionManager()
    st = prog.getSymbolTable()
    mem = prog.getMemory()
    space = prog.getAddressFactory().getDefaultAddressSpace()

    vfts = []
    for sym in st.getAllSymbols(True):
        nm = sym.getName(True)
        if 'vftable' in nm:
            vfts.append((nm, sym.getAddress()))
    print('vftable 符号 %d' % len(vfts), flush=True)

    for out_name, classes in GROUPS.items():
        seeds = []
        for nm, base in vfts:
            # 精确匹配类名,否则 ZcTaskITP 会把 ZcTaskSIMDITP 之外的也带进来
            if not any(('::' + c + '::') in nm or nm.endswith(c + '::vftable') for c in classes):
                continue
            for i in range(60):
                try:
                    ptr = mem.getLong(base.add(i * 8)) & 0xffffffffffffffff
                except Exception:
                    break
                if not (0x140000000 <= ptr < 0x150000000):
                    if i > 0:
                        break
                    continue
                fn = fm.getFunctionAt(space.getAddress(ptr))
                if fn is None:
                    if i > 0:
                        break
                    continue
                seeds.append((fn.getEntryPoint(), nm.split('::')[-2], i))
        print('%s: 种子 %d' % (out_name, len(seeds)), flush=True)

        seen, order = set(), []
        frontier = [(a, 0, tag, slot) for a, tag, slot in seeds]
        while frontier and len(order) < MAXF:
            a, d, tag, slot = frontier.pop(0)
            fn = fm.getFunctionContaining(a)
            if fn is None:
                continue
            ep = fn.getEntryPoint().getOffset()
            if ep in seen:
                continue
            seen.add(ep)
            order.append((fn, tag, slot if d == 0 else -1))
            if d < MAXD:
                for c in fn.getCalledFunctions(mon):
                    if c.getEntryPoint().getOffset() not in seen:
                        frontier.append((c.getEntryPoint(), d + 1, tag, -1))

        with open(os.path.join(SCR, out_name), 'w', encoding='utf-8') as f:
            f.write('// %s - %d functions from %s\n\n' % (out_name, len(order), ', '.join(classes)))
            for fn, tag, slot in order:
                try:
                    r = dec.decompileFunction(fn, 180, mon)
                    c = r.getDecompiledFunction().getC() if r.decompileCompleted() else '// FAILED\n'
                except Exception as e:
                    c = '// EXC %s\n' % e
                f.write('// ===== [%s%s] %s @ 0x%x size=%d =====\n' % (
                    tag, (' slot%d' % slot) if slot >= 0 else '',
                    fn.getName(), fn.getEntryPoint().getOffset(), fn.getBody().getNumAddresses()))
                f.write(c + '\n\n')
        print('WROTE %s (%d functions)' % (out_name, len(order)), flush=True)
