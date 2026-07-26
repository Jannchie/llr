r"""反编译 LinearMatrix16 参数区的解包函数 +0x179390。

它是唯一写参数区的地方,把 ARW 的 276 字节(tag 0x780f)按位域拆成 6x16 系数。
`sr2.py` 的 `unpack_param_block` 照着它写,但实测解出来的表与引擎内存对不上,
所以要照原文重读一遍位域顺序。
"""
import os

SCR = os.path.dirname(os.path.abspath(__file__))
os.environ['GHIDRA_INSTALL_DIR'] = os.path.join(SCR, 'ghidra_12.1.2_PUBLIC')
os.environ['JAVA_HOME'] = os.path.join(SCR, 'jdk-21.0.11+10')
os.environ['PATH'] = os.path.join(SCR, 'jdk-21.0.11+10', 'bin') + os.pathsep + os.environ['PATH']
import pyghidra  # noqa: E402

pyghidra.start()
BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'
WANT = [0x140179390, 0x1403810b0]

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
    space = prog.getAddressFactory().getDefaultAddressSpace()
    with open(os.path.join(SCR, 'unpack_decomp.c'), 'w', encoding='utf-8') as f:
        for a in WANT:
            fn = fm.getFunctionContaining(space.getAddress(a))
            if fn is None:
                f.write('// 0x%x: no function\n' % a)
                continue
            r = dec.decompileFunction(fn, 240, mon)
            c = r.getDecompiledFunction().getC() if r.decompileCompleted() else '// FAILED\n'
            f.write('// ===== %s @ 0x%x size=%d =====\n%s\n\n'
                    % (fn.getName(), fn.getEntryPoint().getOffset(),
                       fn.getBody().getNumAddresses(), c))
            print('WROTE 0x%x (%d bytes)' % (a, fn.getBody().getNumAddresses()), flush=True)
