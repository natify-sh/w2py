# pyright: basic, reportArgumentType=false, reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportSelfClsParameterName=false
"""Wasm binary front end: byte reader, section parser, function-body decoder (ported from poc/w2py.py)."""

import struct

M32, M64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF
I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C
TN = {I32: "i32", I64: "i64", F32: "f32", F64: "f64", 0x70: "ref", 0x6F: "ref"}


class R:
    def __init__(s, b, p=0):
        s.b, s.p = b, p

    def u8(s):
        v = s.b[s.p]
        s.p += 1
        return v

    def u(s):
        r = sh = 0
        while True:
            x = s.b[s.p]
            s.p += 1
            r |= (x & 0x7F) << sh
            sh += 7
            if x < 0x80:
                return r

    def s(s, bits):
        r = sh = 0
        while True:
            x = s.b[s.p]
            s.p += 1
            r |= (x & 0x7F) << sh
            sh += 7
            if x < 0x80:
                break
        if x & 0x40:
            r -= 1 << sh
        return r

    def bytes(s, n):
        v = s.b[s.p : s.p + n]
        s.p += n
        return v

    def name(s):
        return s.bytes(s.u()).decode()


def parse(data):
    r = R(data, 8)
    assert data[:4] == b"\0asm"
    mod = dict(
        types=[],
        imports=[],
        funcs=[],
        tables=[],
        mems=[],
        globals=[],
        exports=[],
        start=None,
        elems=[],
        codes=[],
        datas=[],
    )

    def initexpr():
        op = r.u8()
        if op == 0x41:
            v = ("i32", r.s(32) & M32)
        elif op == 0x42:
            v = ("i64", r.s(64) & M64)
        elif op == 0x43:
            v = ("f32", struct.unpack("<f", r.bytes(4))[0])
        elif op == 0x44:
            v = ("f64", struct.unpack("<d", r.bytes(8))[0])
        elif op == 0x23:
            v = ("global", r.u())
        elif op == 0xD2:
            v = ("func", r.u())
        else:
            raise NotImplementedError("initexpr %x" % op)
        assert r.u8() == 0x0B
        return v

    while r.p < len(data):
        sid = r.u8()
        size = r.u()
        end = r.p + size
        if sid == 1:
            for _ in range(r.u()):
                assert r.u8() == 0x60
                ps = [r.u8() for _ in range(r.u())]
                rs = [r.u8() for _ in range(r.u())]
                mod["types"].append((ps, rs))
        elif sid == 2:
            for _ in range(r.u()):
                m, n, k = r.name(), r.name(), r.u8()
                if k == 0:
                    mod["imports"].append(("func", m, n, r.u()))
                elif k == 1:
                    r.u8()
                    fl = r.u8()
                    mn = r.u()
                    mx = r.u() if fl & 1 else None
                    mod["imports"].append(("table", m, n, (mn, mx)))
                elif k == 2:
                    fl = r.u8()
                    mn = r.u()
                    mx = r.u() if fl & 1 else None
                    mod["imports"].append(("mem", m, n, (mn, mx)))
                elif k == 3:
                    t = r.u8()
                    mut = r.u8()
                    mod["imports"].append(("global", m, n, (t, mut)))
                else:
                    raise NotImplementedError("import kind %d" % k)
        elif sid == 3:
            mod["funcs"] = [r.u() for _ in range(r.u())]
        elif sid == 4:
            for _ in range(r.u()):
                t = r.u8()
                fl = r.u8()
                mn = r.u()
                mx = r.u() if fl & 1 else None
                mod["tables"].append((mn, mx))
        elif sid == 5:
            for _ in range(r.u()):
                fl = r.u8()
                mn = r.u()
                mx = r.u() if fl & 1 else None
                mod["mems"].append((mn, mx))
        elif sid == 6:
            for _ in range(r.u()):
                t = r.u8()
                mut = r.u8()
                mod["globals"].append((t, mut, initexpr()))
        elif sid == 7:
            for _ in range(r.u()):
                n = r.name()
                k = r.u8()
                i = r.u()
                mod["exports"].append((n, k, i))
        elif sid == 8:
            mod["start"] = r.u()
        elif sid == 9:
            for _ in range(r.u()):
                fl = r.u()
                if fl == 0:
                    off = initexpr()
                    fs = [r.u() for _ in range(r.u())]
                    mod["elems"].append((0, off, fs))
                elif fl == 2:
                    ti = r.u()
                    off = initexpr()
                    assert r.u8() == 0
                    fs = [r.u() for _ in range(r.u())]
                    mod["elems"].append((ti, off, fs))
                elif fl in (1, 3):
                    assert r.u8() == 0
                    [r.u() for _ in range(r.u())]  # passive/declarative: ignore
                else:
                    raise NotImplementedError("elem flags %d" % fl)
        elif sid == 10:
            for _ in range(r.u()):
                sz = r.u()
                e = r.p + sz
                locs = []
                for _ in range(r.u()):
                    n = r.u()
                    t = r.u8()
                    locs += [t] * n
                mod["codes"].append((locs, data[r.p : e]))
                r.p = e
        elif sid == 11:
            for _ in range(r.u()):
                fl = r.u()
                if fl == 0:
                    off = initexpr()
                    d = r.bytes(r.u())
                    mod["datas"].append((off, d))
                elif fl == 1:
                    d = r.bytes(r.u())
                    mod["datas"].append((None, d))
                elif fl == 2:
                    r.u()
                    off = initexpr()
                    d = r.bytes(r.u())
                    mod["datas"].append((off, d))
        r.p = end
    return mod


# ----------------------------------------------------------------------------- instruction decode
MEMOPS = set(range(0x28, 0x3F))


def decode(body):
    r = R(body)
    out = []
    while r.p < len(body):
        op = r.u8()
        if op in (0x02, 0x03, 0x04):
            b = body[r.p]
            if b == 0x40:
                r.p += 1
                bt = ([], [])
            elif b in TN:
                r.p += 1
                bt = ([], [b])
            else:
                bt = ("idx", r.s(33))
            out.append((op, bt))
        elif op in (0x0C, 0x0D, 0x10, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0xD2):
            out.append((op, r.u()))
        elif op == 0x0E:
            ls = [r.u() for _ in range(r.u())]
            out.append((op, ls, r.u()))
        elif op == 0x11:
            ti = r.u()
            tb = r.u()
            out.append((op, ti, tb))
        elif op in MEMOPS:
            al = r.u()
            off = r.u()
            out.append((op, off, al))
        elif op in (0x3F, 0x40):
            r.u8()
            out.append((op,))
        elif op == 0x41:
            out.append((op, r.s(32) & M32))
        elif op == 0x42:
            out.append((op, r.s(64) & M64))
        elif op == 0x43:
            out.append((op, struct.unpack("<f", r.bytes(4))[0]))
        elif op == 0x44:
            out.append((op, struct.unpack("<d", r.bytes(8))[0]))
        elif op == 0x1C:
            [r.u8() for _ in range(r.u())]
            out.append((0x1B,))
        elif op == 0xD0:
            r.u8()
            out.append((op,))
        elif op == 0xFC:
            sub = r.u()
            if sub <= 7:
                out.append((0xFC00 + sub,))
            elif sub == 8:
                seg = r.u()
                r.u8()
                out.append((0xFC08, seg))
            elif sub == 9:
                out.append((0xFC09, r.u()))
            elif sub == 10:
                r.u8()
                r.u8()
                out.append((0xFC0A,))
            elif sub == 11:
                r.u8()
                out.append((0xFC0B,))
            else:
                raise NotImplementedError("0xFC %d" % sub)
        else:
            out.append((op,))
    return out
