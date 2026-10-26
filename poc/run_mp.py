# Run a w2py-translated CoreMark module under MicroPython: micropython run_mp.py <dir> <module>
import sys, time
sys.path.insert(0, sys.argv[1])
t = time.ticks_ms()
m = __import__(sys.argv[2])
print('[mp] import/compile %d ms' % time.ticks_diff(time.ticks_ms(), t))
T0 = time.ticks_ms()
def millis(): return time.ticks_diff(time.ticks_ms(), T0) & 0xFFFFFFFF
def pr(p, n): sys.stdout.write(bytes(m.M[p:p + n]).decode())
ex = m.instantiate({'wiring': {'millis': millis, 'print': pr}})
t = time.ticks_ms(); ex['setup']()
print('\n[mp] wall %d ms' % time.ticks_diff(time.ticks_ms(), t))
