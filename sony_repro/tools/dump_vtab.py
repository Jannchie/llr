r"""按字节偏移把 vftable 打出来 —— 反编译里的 `*param_1 + 0x110` 要能直接查到函数。

反编译输出里的 slotN 是我 BFS 的序号,去重之后会跳号,不能当偏移用。
"""
import os
import sys

SCR = os.path.dirname(os.path.abspath(__file__))
os.environ['GHIDRA_INSTALL_DIR'] = os.path.join(SCR, 'ghidra_12.1.2_PUBLIC')
os.environ['JAVA_HOME'] = os.path.join(SCR, 'jdk-21.0.11+10')
os.environ['PATH'] = os.path.join(SCR, 'jdk-21.0.11+10', 'bin') + os.pathsep + os.environ['PATH']
import pyghidra  # noqa: E402

pyghidra.start()
BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
WANT = sys.argv[1:] or ['ZcTaskITP', 'ZcTaskAreaComp', 'ZcTaskSIMDITP', 'ZcTaskAreaCompSIMD']

with pyghidra.open_program(BIN, project_location=os.path.join(SCR, 'pygproj'),
                           project_name='edit', analyze=False) as flat:
    prog = flat.getCurrentProgram()
    fm = prog.getFunctionManager()
    st = prog.getSymbolTable()
    mem = prog.getMemory()
    space = prog.getAddressFactory().getDefaultAddressSpace()

    for sym in st.getAllSymbols(True):
        nm = sym.getName(True)
        if 'vftable' not in nm or '::' not in nm:
            continue
        parts = nm.split('::')
        cls = parts[-2] if len(parts) >= 2 else ''
        if cls not in WANT:
            continue
        base = sym.getAddress()
        print('\n=== %s @ %s ===' % (nm, base))
        for i in range(64):
            try:
                ptr = mem.getLong(base.add(i * 8)) & 0xffffffffffffffff
            except Exception:
                break
            if not (0x140000000 <= ptr < 0x150000000):
                print('  +0x%-4x  (non-code 0x%x)' % (i * 8, ptr))
                break
            fn = fm.getFunctionAt(space.getAddress(ptr))
            if fn is None:
                print('  +0x%-4x  0x%x (no-func)' % (i * 8, ptr))
                continue
            print('  +0x%-4x  %-16s 0x%x  %d bytes'
                  % (i * 8, fn.getName(), ptr, fn.getBody().getNumAddresses()))
