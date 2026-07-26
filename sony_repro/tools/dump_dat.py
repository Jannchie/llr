r"""把反编译里出现的 DAT_ 常量按 float / double / int 三种解释打出来。

Ghidra 的反编译只给地址不给值,而这些常量就是整个阶段的全部参数。
用法: python dump_dat.py 1404debd0 1404ded88 ...
      python dump_dat.py --table 1405a93c0 512
"""
import os
import struct
import sys

SCR = os.path.dirname(os.path.abspath(__file__))
os.environ['GHIDRA_INSTALL_DIR'] = os.path.join(SCR, 'ghidra_12.1.2_PUBLIC')
os.environ['JAVA_HOME'] = os.path.join(SCR, 'jdk-21.0.11+10')
os.environ['PATH'] = os.path.join(SCR, 'jdk-21.0.11+10', 'bin') + os.pathsep + os.environ['PATH']
import pyghidra  # noqa: E402

pyghidra.start()
BIN = r'C:\Program Files\Sony\Imaging Edge\Edit.exe'


def read(mem, space, addr, n):
    buf = bytearray(n)
    for i in range(n):
        buf[i] = mem.getByte(space.getAddress(addr + i)) & 0xFF
    return bytes(buf)


with pyghidra.open_program(BIN, project_location=os.path.join(SCR, 'pygproj'),
                           project_name='edit', analyze=False) as flat:
    prog = flat.getCurrentProgram()
    mem = prog.getMemory()
    space = prog.getAddressFactory().getDefaultAddressSpace()

    args = sys.argv[1:]
    if args and args[0] == '--table':
        addr, n = int(args[1], 16), int(args[2])
        raw = read(mem, space, addr, n * 4)
        vals = struct.unpack('<%df' % n, raw)
        for i in range(0, n, 8):
            print('[%3d] %s' % (i, ' '.join('%9.6f' % v for v in vals[i:i + 8])))
    else:
        for a in args:
            addr = int(a, 16)
            raw = read(mem, space, addr, 8)
            f = struct.unpack('<f', raw[:4])[0]
            d = struct.unpack('<d', raw)[0]
            i4 = struct.unpack('<i', raw[:4])[0]
            print('0x%x  float=%-14g double=%-18g int32=%-12d raw=%s'
                  % (addr, f, d, i4, raw.hex()))
