"""w2luajit: WebAssembly -> LuaJIT 2.1 backend (subclass of the Lua 5.4 generator).

LuaJIT is Lua 5.1 + goto: numbers are doubles, bit ops come from `bit` and return signed int32.
  * i32 = canonical unsigned double in [0, 2^32); a "dirty" value is any exact integer double
    congruent mod 2^32 (sums, signed bit-op results).  Normalisation: `x % 4294967296`
    (or bit.tobit where a signed view is wanted).  bit.* accept dirty inputs directly.
  * i32 mul: exact via 16-bit split (a*b can exceed 2^53).
  * i64 = FFI int64_t cdata (wraps like wasm); unsigned compare/div through uint64_t casts.
  * memory = calloc'ed FFI buffer reserved for the full 4 GiB address space (lazily committed),
    accessed through typed pointer casts.  Accesses past memory.size read/write the reservation
    instead of trapping (PoC limitation).
"""
import re, sys
from w2py import parse, E, dep, I32, I64, F32, F64, M32, M64
import poc.w2lua as w2lua
from poc.w2lua import LuaFuncGen, LuaModGen, lflit

MOD = '4294967296'


class JitFuncGen(LuaFuncGen):
    def n(s, e):
        if e.t != I32 or e.b is not None: return e.c
        return '(%s %% %s)' % (e.c, MOD)

    def sgn(s, e):
        if e.t != I32: return e.c
        if e.b is not None and e.b < 0x80000000: return e.c
        return 'tobit(%s)' % e.c

    def cond(s, e):
        if e.t == 'bool': return e.c
        if e.t in (F32, F64): return '(%s ~= 0)' % e.c
        if e.t == I64: return '(%s ~= 0)' % e.c
        if e.b is not None: return '(%s ~= 0)' % e.c
        return '(tobit(%s) ~= 0)' % e.c

    # memory: typed pointer casts on the FFI buffer
    def load(s, op, a, off):
        s.uses_mem = True
        ad = s.n(a) if off == 0 else '%s + %d' % (s.n(a), off)
        d = dep(a, m=True)
        T = {0x28: ('pi32', I32, None), 0x29: ('pi64', I64, None), 0x2A: ('pf32', F32, None),
             0x2B: ('pf64', F64, None), 0x2C: ('pi8', I32, None), 0x2D: (None, I32, 255),
             0x2E: ('pi16', I32, None), 0x2F: ('pu16', I32, 0xFFFF), 0x30: ('pi8', I64, None),
             0x31: (None, I64, None), 0x32: ('pi16', I64, None), 0x33: ('pu16', I64, None),
             0x34: ('pi32', I64, None), 0x35: ('pu32', I64, None)}[op]
        ptr, ty, b = T
        c = 'M[%s]' % ad if ptr is None else 'cast(%s, M + %s)[0]' % (ptr, ad)
        if ty == I64 and op != 0x29: c = 'i64(%s)' % c
        return E(c, ty, b, **d)

    def _store(s, op, a, v, off, base):
        ad = base if off == 0 else '%s + %d' % (base, off)
        if op in (0x3A, 0x3C):
            if op == 0x3C: vc = 'tonumber(band(%s, 0xFF))' % v.c
            else: vc = v.c if v.b is not None and v.b <= 255 else 'band(%s, 0xFF)' % v.c
            s.emit('M[%s] = %s' % (ad, vc)); return  # uint8_t store truncates mod 256
        ptr = {0x36: 'pi32', 0x37: 'pi64', 0x38: 'pf32', 0x39: 'pf64', 0x3B: 'pu16', 0x3D: 'pu16', 0x3E: 'pu32'}[op]
        vc = v.c
        if op == 0x36: vc = 'tobit(%s)' % v.c if not (v.b is not None and v.b < 0x80000000) else v.c
        elif op == 0x3B: vc = 'band(%s, 0xFFFF)' % v.c
        elif op == 0x3D: vc = 'tonumber(band(%s, 0xFFFF))' % v.c
        elif op == 0x3E: vc = 'tonumber(band(%s, 0xFFFFFFFF)) %% %s' % (v.c, MOD)
        s.emit('cast(%s, M + %s)[0] = %s' % (ptr, ad, vc))

    def store(s, op, a, v, off):
        s.uses_mem = True
        saved = s.keep(a.c, v.c)
        base = s.n(a)
        try:
            s._store(op, a, v, off, base)
        finally:
            s.reserved = saved

    def simple(s, x):
        op = x[0]
        if op == 0x42:
            v = x[1]
            if v >= 1 << 63: v -= 1 << 64
            s.push(E('(-9223372036854775807LL-1)' if v == -(1 << 63) else '%dLL' % v, I64, None)); return
        return LuaFuncGen.simple(s, x)

    def int_op(s, op):
        push, pop, n, nb, sgn = s.push, s.pop, s.n, s.nb, s.sgn
        if op == 0x45:
            a = s.popc()
            if a.t == 'bool': push(E('(not %s)' % a.c, 'bool', 1, **dep(a))); return True
            c = '(%s == 0)' % a.c if a.b is not None else '(tobit(%s) == 0)' % a.c
            push(E(c, 'bool', 1, **dep(a))); return True
        if op == 0x50:
            a = pop(); push(E('(%s == 0)' % a.c, 'bool', 1, **dep(a))); return True
        if 0x46 <= op <= 0x4F:
            b = pop(); a = pop(); k = op - 0x46; d = dep(a, b)
            if k in (0, 1):
                if a.b is not None and b.b is not None: c = '(%s %s %s)' % (a.c, '==' if k == 0 else '~=', b.c)
                else: c = '(bxor(%s, %s) %s 0)' % (a.c, b.c, '==' if k == 0 else '~=')
            else:
                o = ['<', '<', '>', '>', '<=', '<=', '>=', '>='][k - 2]
                if k in (2, 4, 6, 8): c = '(%s %s %s)' % (sgn(a), o, sgn(b))
                else: c = '(%s %s %s)' % (n(a), o, n(b))
            push(E(c, 'bool', 1, **d)); return True
        if 0x51 <= op <= 0x5A:
            b = pop(); a = pop(); k = op - 0x51; d = dep(a, b)
            if k in (0, 1): c = '(%s %s %s)' % (a.c, '==' if k == 0 else '~=', b.c)
            else:
                o = ['<', '<', '>', '>', '<=', '<=', '>=', '>='][k - 2]
                if k in (2, 4, 6, 8): c = '(%s %s %s)' % (a.c, o, b.c)
                else: c = '(u64(%s) %s u64(%s))' % (a.c, o, b.c)
            push(E(c, 'bool', 1, **d)); return True
        if 0x67 <= op <= 0x78:
            k = op - 0x67
            if k <= 2:
                a = pop(); push(E('%s(%s)' % (['clz32', 'ctz32', 'popcnt32'][k], n(a)), I32, 32, **dep(a))); return True
            if k in (10, 11, 12) and s.stack[-1].t == 'bool' and s.stack[-2].t == 'bool':
                b = s.popc(); a = s.popc()
                push(E('(%s %s %s)' % (a.c, {10: 'and', 11: 'or', 12: '~='}[k], b.c), 'bool', 1, **dep(a, b))); return True
            b = pop(); a = pop(); d = dep(a, b)
            def isc(e): return e.pure_const and e.c.isdigit()
            if k == 3:
                if isc(b) and b.c == '0': push(a); return True
                bb = a.b + b.b if a.b is not None and b.b is not None and a.b + b.b <= M32 else None
                push(E('(%s + %s)' % (a.c, b.c), I32, bb, **d)); return True
            if k == 4:
                if isc(b) and b.c == '0': push(a); return True
                push(E('(%s - %s)' % (a.c, b.c), I32, None, **d)); return True
            if k == 5:
                if a.b is not None and b.b is not None and a.b * b.b < (1 << 53):
                    bb = a.b * b.b
                    push(E('(%s * %s)' % (a.c, b.c), I32, bb if bb <= M32 else None, **d)); return True
                push(E('mul32(%s, %s)' % (n(a), n(b)), I32, M32, **d)); return True
            if k == 6: push(E('divs32(%s, %s)' % (n(a), n(b)), I32, M32, **d)); return True
            if k == 7: push(E('divu32(%s, %s)' % (n(a), n(b)), I32, nb(a), **d)); return True
            if k == 8: push(E('rems32(%s, %s)' % (n(a), n(b)), I32, M32, **d)); return True
            if k == 9: push(E('remu32(%s, %s)' % (n(a), n(b)), I32, nb(a), **d)); return True
            if k == 10:
                # band with a non-negative int32 operand is non-negative and bounded by it
                bs = [e.b for e in (a, b) if e.b is not None and e.b < 0x80000000]
                push(E('band(%s, %s)' % (a.c, b.c), I32, min(bs) if bs else None, **d)); return True
            if k in (11, 12):
                bb = None
                if a.b is not None and b.b is not None and max(a.b, b.b) < 0x80000000:
                    bb = (1 << max(a.b, b.b).bit_length()) - 1
                push(E('%s(%s, %s)' % ('bor' if k == 11 else 'bxor', a.c, b.c), I32, bb, **d)); return True
            cnt = int(b.c) & 31 if isc(b) else None
            if k == 13:
                bb = a.b << cnt if cnt is not None and a.b is not None and (a.b << cnt) < 0x80000000 else None
                push(E('lshift(%s, %s)' % (a.c, b.c), I32, bb, **d)); return True
            if k == 14: push(E('arshift(%s, %s)' % (a.c, b.c), I32, None, **d)); return True
            if k == 15:
                if cnt is not None and cnt > 0:
                    push(E('rshift(%s, %d)' % (a.c, cnt), I32, min(nb(a) >> cnt, M32 >> cnt), **d)); return True
                push(E('rshift(%s, %s)' % (a.c, b.c), I32, None, **d)); return True
            if k == 16: push(E('rol(%s, %s)' % (a.c, b.c), I32, None, **d)); return True
            if k == 17: push(E('ror(%s, %s)' % (a.c, b.c), I32, None, **d)); return True
        if 0x79 <= op <= 0x8A:
            k = op - 0x79
            if k <= 2:
                a = pop(); push(E('%s(%s)' % (['clz64', 'ctz64', 'popcnt64'][k], a.c), I64, None, **dep(a))); return True
            b = pop(); a = pop(); d = dep(a, b)
            o = {3: '+', 4: '-', 5: '*'}
            if k in o: push(E('(%s %s %s)' % (a.c, o[k], b.c), I64, None, **d)); return True
            f = {6: 'divs64', 7: 'divu64', 8: 'rems64', 9: 'remu64', 10: 'band', 11: 'bor', 12: 'bxor',
                 13: 'lshift', 14: 'arshift', 15: 'rshift', 16: 'rol', 17: 'ror'}[k]
            if k >= 13: push(E('%s(%s, tonumber(band(%s, 63)))' % (f, a.c, b.c), I64, None, **d)); return True
            push(E('%s(%s, %s)' % (f, a.c, b.c), I64, None, **d)); return True
        if op == 0xA7: a = pop(); push(E('tonumber(band(%s, 0xFFFFFFFFLL))' % a.c, I32, M32, **dep(a))); return True
        if op == 0xAC: a = pop(); push(E('i64(%s)' % sgn(a), I64, None, **dep(a))); return True
        if op == 0xAD: a = pop(); push(E('i64(%s)' % n(a), I64, None, **dep(a))); return True
        if op in (0xC0, 0xC1):
            a = pop(); k = 24 if op == 0xC0 else 16
            push(E('arshift(lshift(%s, %d), %d)' % (a.c, k, k), I32, None, **dep(a))); return True
        if op in (0xC2, 0xC3, 0xC4):
            a = pop(); k = {0xC2: 56, 0xC3: 48, 0xC4: 32}[op]
            push(E('arshift(lshift(%s, %d), %d)' % (a.c, k, k), I64, None, **dep(a))); return True
        if op == 0xFC0A:
            nn = pop(); src = pop(); dst = pop(); s.flush(lambda e: e.m)
            s.emit('mcopy(%s, %s, %s)' % (n(dst), n(src), n(nn))); return True
        if op == 0xFC0B:
            nn = pop(); v = pop(); dst = pop(); s.flush(lambda e: e.m)
            s.emit('mfill(%s, %s, %s)' % (n(dst), n(v), n(nn))); return True
        return False

    def float_op(s, op):
        push, pop, n, sgn = s.push, s.pop, s.n, s.sgn
        conv = {
            0xB2: ('f32(%s)', F32, 's'), 0xB3: ('f32(%s)', F32, 'u'), 0xB4: ('f32(tonumber(%s))', F32, 'l'),
            0xB5: ('f32(u64tof(%s))', F32, 'l'), 0xB7: ('(%s + 0.0)', F64, 's'), 0xB8: ('(%s + 0.0)', F64, 'u'),
            0xB9: ('tonumber(%s)', F64, 'l'), 0xBA: ('u64tof(%s)', F64, 'l'),
        }
        if op in conv:
            f, T, src = conv[op]; a = pop()
            arg = {'s': sgn(a), 'u': n(a), 'l': a.c}[src]
            push(E(f % arg, T, None, **dep(a))); return True
        if op in (0xBE,):
            a = pop(); push(E('f32_i32(%s)' % n(a), F32, None, **dep(a))); return True
        return LuaFuncGen.float_op(s, op)


JIT_RT = r'''
local ffi = require("ffi")
local bit = require("bit")
local band, bor, bxor, lshift, rshift, arshift, tobit, rol, ror =
  bit.band, bit.bor, bit.bxor, bit.lshift, bit.rshift, bit.arshift, bit.tobit, bit.rol, bit.ror
local floor, ceil, sqrt, huge, fmod = math.floor, math.ceil, math.sqrt, math.huge, math.fmod
local cast = ffi.cast
pcall(ffi.cdef, "void *calloc(size_t, size_t);")
local pi8, pi16, pu16, pi32, pu32, pi64, pf32, pf64 = ffi.typeof("int8_t*"), ffi.typeof("int16_t*"),
  ffi.typeof("uint16_t*"), ffi.typeof("int32_t*"), ffi.typeof("uint32_t*"), ffi.typeof("int64_t*"),
  ffi.typeof("float*"), ffi.typeof("double*")
local i64t, u64t = ffi.typeof("int64_t"), ffi.typeof("uint64_t")
local function i64(x) return i64t(x) end
local function u64(x) return cast(u64t, x) end
-- reserve the whole 32-bit address space (+ max offset slack); pages are committed lazily
local MEMBASE = ffi.C.calloc(4294967296 + 1048576, 1)
assert(MEMBASE ~= nil, "cannot reserve linear memory")
local MEM = cast("uint8_t*", MEMBASE)
local MEMPAGES, MAXPAGES = @PAGES@, @MAXP@
local function trap(msg) error("wasm trap: " .. msg, 0) end
local function mgrow(n)
  local old = MEMPAGES
  if old + n > MAXPAGES then return 4294967295 end
  MEMPAGES = old + n
  return old
end
local function mcopy(d, s, n)
  if d + n > MEMPAGES * 65536 or s + n > MEMPAGES * 65536 then trap("oob") end
  ffi.copy(MEM + d, MEM + s, n)
end
local function mfill(d, v, n)
  if d + n > MEMPAGES * 65536 then trap("oob") end
  ffi.fill(MEM + d, n, band(v, 0xFF))
end
local function mul32(a, b)
  local lo = b % 65536
  local hi = (b - lo) / 65536
  return (a * lo + ((a * hi) % 65536) * 65536) % 4294967296
end
local function divu32(a, b) if b == 0 then trap("integer divide by zero") end return floor(a / b) end
local function remu32(a, b) if b == 0 then trap("integer divide by zero") end return a % b end
local function divs32(a, b)
  a = tobit(a); b = tobit(b)
  if b == 0 then trap("integer divide by zero") end
  if a == -2147483648 and b == -1 then trap("integer overflow") end
  local q = a / b
  q = q >= 0 and floor(q) or ceil(q)
  return q % 4294967296
end
local function rems32(a, b)
  a = tobit(a); b = tobit(b)
  if b == 0 then trap("integer divide by zero") end
  return fmod(a, b) % 4294967296
end
local function clz32(x) if x == 0 then return 32 end local n = 0
  while x < 2147483648 do x = x * 2; n = n + 1 end return n end
local function ctz32(x) if x == 0 then return 32 end local n = 0
  while x % 2 == 0 do x = x / 2; n = n + 1 end return n end
local function popcnt32(x) local n = 0 x = tobit(x) while x ~= 0 do x = band(x, x - 1); n = n + 1 end return n end
local function clz64(x) local n = 0 if x == 0 then return i64(64) end
  while band(x, i64(1) * 2^62 * 2) == 0 do x = lshift(x, 1); n = n + 1 end return i64(n) end
local function ctz64(x) local n = 0 if x == 0 then return i64(64) end
  while band(x, 1) == 0 do x = rshift(x, 1); n = n + 1 end return i64(n) end
local function popcnt64(x) local n = 0 while x ~= 0 do x = band(x, x - 1); n = n + 1 end return i64(n) end
local function divs64(a, b)
  if b == 0 then trap("integer divide by zero") end
  if a == i64(-9223372036854775807LL - 1) and b == -1 then trap("integer overflow") end
  return a / b end
local function rems64(a, b) if b == 0 then trap("integer divide by zero") end
  if b == -1 then return i64(0) end return a % b end
local function divu64(a, b) if b == 0 then trap("integer divide by zero") end return i64(u64(a) / u64(b)) end
local function remu64(a, b) if b == 0 then trap("integer divide by zero") end return i64(u64(a) % u64(b)) end
local scratch = ffi.new("union { float f; double d; int32_t i; uint32_t u; int64_t l; }")
local function f32(x) scratch.f = x return scratch.f end
local function i32_f32(x) scratch.f = x return scratch.u end
local function f32_i32(x) scratch.u = x return scratch.f end
local function i64_f64(x) scratch.d = x return scratch.l end
local function f64_i64(x) scratch.l = x return scratch.d end
local function u64tof(x) return tonumber(u64(x)) end
local function fin(x) return x == x and x ~= huge and x ~= -huge end
local function ffloor(x) if not fin(x) or x == 0 then return x end return floor(x) end
local function fceil(x) if not fin(x) or x == 0 then return x end local r = ceil(x)
  if r == 0 and x < 0 then return -0.0 end return r end
local function ftrunc(x) if x >= 0 then return ffloor(x) else return fceil(x) end end
local function fnearest(x)
  if not fin(x) or x == 0 then return x end
  local r = floor(x + 0.5)
  if r - x == 0.5 and r % 2 ~= 0 then r = r - 1 end
  if r == 0 and x < 0 then return -0.0 end
  return r
end
local function fsqrt(x) if x < 0 then return 0/0 end return sqrt(x) end
local function fmin(a, b) if a ~= a or b ~= b then return 0/0 end
  if a == 0 and b == 0 then return (1/a < 0) and a or b end return a < b and a or b end
local function fmax(a, b) if a ~= a or b ~= b then return 0/0 end
  if a == 0 and b == 0 then return (1/a < 0) and b or a end return a > b and a or b end
local function fcopysign(a, b) a = math.abs(a)
  if b < 0 or (b == 0 and 1/b < 0) then return -a end return a end
local function trunc_any(x, lo, hi)
  if x ~= x then trap("invalid conversion to integer") end
  local t = x >= 0 and floor(x) or ceil(x)
  if not (t >= lo and t <= hi) then trap("integer overflow") end
  return t
end
local function trunc_s32(x) return trunc_any(x, -2147483648, 2147483647) % 4294967296 end
local function trunc_u32(x) return trunc_any(x, 0, 4294967295) end
local function trunc_s64(x) return i64(trunc_any(x, -9223372036854775808, 9223372036854775807)) end
local function trunc_u64(x) return i64(cast(u64t, trunc_any(x, 0, 18446744073709551615))) end
local function tsat_s32(x) if x ~= x then return 0 end if x >= 2147483647 then return 2147483647 end
  if x <= -2147483648 then return 2147483648 end return trunc_s32(x) end
local function tsat_u32(x) if x ~= x or x <= 0 then return 0 end if x >= 4294967295 then return 4294967295 end
  return trunc_u32(x) end
local function tsat_s64(x) if x ~= x then return i64(0) end return trunc_s64(math.max(math.min(x, 9.2233720368547748e18), -9223372036854775808)) end
local function tsat_u64(x) if x ~= x or x <= 0 then return i64(0) end return trunc_u64(math.min(x, 1.8446744073709550e19)) end
local function memread(p, n) return ffi.string(MEM + p, n) end
'''


class JitModGen(LuaModGen):
    FuncGen = JitFuncGen
    RT = JIT_RT

    def gconst(s, t, v):
        if t == I64:
            if v >= 1 << 63: v -= 1 << 64
            return '%dLL' % v
        if t in (F32, F64): return lflit(v)
        return str(v)

    def data_line(s, off, d):
        return 'ffi.copy(MEM + %d, %s, %d)' % (off, w2lua.lua_str(d), len(d))

if __name__ == '__main__':
    mod = parse(open(sys.argv[1], 'rb').read())
    open(sys.argv[2], 'w').write(JitModGen(mod).gen())
    print(sys.argv[2])
