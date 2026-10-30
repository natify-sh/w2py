/* Minimal iwasm host: runs <func> [i32 arg] of a .wasm/.aot; provides wiring.millis / wiring.print for CoreMark. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "wasm_export.h"

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static double T0;
static uint32_t millis(wasm_exec_env_t e) { return (uint32_t)((now() - T0) * 1000); }
static void print(wasm_exec_env_t e, const char *p, uint32_t n) { fwrite(p, 1, n, stdout); }
static NativeSymbol syms[] = { {"millis", millis, "()i", NULL}, {"print", print, "(*~)", NULL} };

int main(int argc, char **argv) {
    char err[256]; FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *buf = malloc(n); fread(buf, 1, n, f); fclose(f);
    RuntimeInitArgs a; memset(&a, 0, sizeof a); a.mem_alloc_type = Alloc_With_System_Allocator;
    if (argc > 4) a.running_mode = (RunningMode)atoi(argv[4]);   /* 1 interp, 2 fast-jit, 3 llvm-jit, 4 multi-tier */
    if (!wasm_runtime_full_init(&a)) { puts("init failed"); return 1; }
    wasm_runtime_register_natives("wiring", syms, 2);
    T0 = now(); double t = now();
    wasm_module_t m = wasm_runtime_load(buf, n, err, sizeof err); if (!m) { printf("load: %s\n", err); return 1; }
    wasm_module_inst_t mi = wasm_runtime_instantiate(m, 1 << 20, 1 << 20, err, sizeof err); if (!mi) { printf("inst: %s\n", err); return 1; }
    double tl = now() - t;
    wasm_exec_env_t env = wasm_runtime_create_exec_env(mi, 1 << 20);
    wasm_function_inst_t fn = wasm_runtime_lookup_function(mi, argv[2]);
    uint32_t argv32[1] = { argc > 3 ? (uint32_t)atoi(argv[3]) : 0 };
    t = now();
    if (!wasm_runtime_call_wasm(env, fn, argc > 3 ? 1 : 0, argv32)) { printf("trap: %s\n", wasm_runtime_get_exception(mi)); return 1; }
    printf("\n[iwasm] load %.1f ms, %s: %.3f s, ret %08x\n", tl * 1000, argv[2], now() - t, argv32[0]);
    return 0;
}
