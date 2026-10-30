# WAMR (iwasm) on CoreMark and the switch VM

`host.c` is a minimal embedding of WAMR's `libiwasm`: it provides CoreMark's imports
(`wiring.millis`, `wiring.print`) and calls one export, timing it. The `iwasm` CLI cannot provide
those imports, which is why a host is needed.

```
# runtime: the SAME WAMR version as wamrc (an .aot of 2.4.3 is refused by main: "unknown binary version")
git clone --depth 1 --branch WAMR-2.4.3 https://github.com/bytecodealliance/wasm-micro-runtime wamr
cmake -S wamr/product-mini/platforms/linux -B b -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DWAMR_BUILD_AOT=1 -DWAMR_BUILD_INTERP=1 -DWAMR_BUILD_FAST_INTERP=1 -DWAMR_BUILD_JIT=0 -DWAMR_BUILD_LIBC_WASI=0
ninja -C b vmlib
gcc -O2 -o host host.c -Iwamr/core/iwasm/include b/libiwasm.a -lm -lpthread -ldl

# compiler: prebuilt wamrc 2.4.3 (GitHub release wamrc-2.4.3-x86_64-ubuntu-22.04.tar.gz)
wamrc --target=x86_64 -o core.aot ../../in/core.wasm
./host core.aot setup                     # AOT
./host ../../in/core.wasm setup           # fast interpreter (same build)
# Fast JIT: a separate build with -DWAMR_BUILD_FAST_JIT=1 -DWAMR_BUILD_FAST_INTERP=0 -DWAMR_BUILD_SIMD=0
#           (Fast JIT is incompatible with SIMD), run with a 4th argument 2 (running mode Fast JIT):
./host ../../in/core.wasm setup 0 2
./host vm.aot run 200000                  # the switch VM of ../switch/
```
