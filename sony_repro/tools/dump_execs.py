r"""把每个 ZcTask* 的执行函数(vftable +0x38)的 RVA 导成一张表。

frida 那边要按 RVA 挂钩,而 vftable 符号里带 `_meta_ptr` 后缀的那个整体前移了 8 字节
(它指向 RTTI COL),必须只认不带后缀的。
"""
import json
import os

SCR = os.path.dirname(os.path.abspath(__file__))
os.environ['GHIDRA_INSTALL_DIR'] = os.path.join(SCR, 'ghidra_12.1.2_PUBLIC')
os.environ['JAVA_HOME'] = os.path.join(SCR, 'jdk-21.0.11+10')
os.environ['PATH'] = os.path.join(SCR, 'jdk-21.0.11+10', 'bin') + os.pathsep + os.environ['PATH']
import pyghidra  # noqa: E402

pyghidra.start()
BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
IMAGE_BASE = 0x140000000
EXEC_SLOT = 0x38

with pyghidra.open_program(BIN, project_location=os.path.join(SCR, 'pygproj'),
                           project_name='edit', analyze=False) as flat:
    prog = flat.getCurrentProgram()
    fm = prog.getFunctionManager()
    st = prog.getSymbolTable()
    mem = prog.getMemory()
    space = prog.getAddressFactory().getDefaultAddressSpace()

    out = {}
    for sym in st.getAllSymbols(True):
        nm = sym.getName(True)
        if not nm.endswith('::vftable') or '::ZcTask' not in nm:
            continue
        cls = nm.split('::')[-2]
        try:
            ptr = mem.getLong(sym.getAddress().add(EXEC_SLOT)) & 0xFFFFFFFFFFFFFFFF
        except Exception:
            continue
        if not (IMAGE_BASE <= ptr < IMAGE_BASE + 0x10000000):
            continue
        fn = fm.getFunctionAt(space.getAddress(ptr))
        out[cls] = {'rva': ptr - IMAGE_BASE,
                    'size': fn.getBody().getNumAddresses() if fn else 0}

    path = os.path.join(SCR, 'task_execs.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, sort_keys=True)
    for k in sorted(out):
        print('%-40s rva=0x%-8x %d bytes' % (k, out[k]['rva'], out[k]['size']))
    print('\nWROTE %s (%d)' % (path, len(out)))
