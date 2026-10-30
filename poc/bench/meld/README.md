# meld-core without WASI

A ~40-line wrapper that builds pulseengine's `meld-core` (v0.66.0 tested) as a wasm module with no
imports, used to check that the PoC runs a real Rust program (docs/BENCHMARKS.md, "meld on the PoC").

```
git clone https://github.com/pulseengine/meld ../meld        # next to this folder, as Cargo.toml expects
cargo build --release --target wasm32-unknown-unknown
python3 ../../w2py.py target/wasm32-unknown-unknown/release/meld_nowasi.wasm meld_nowasi.py
cargo build --manifest-path ../meld/Cargo.toml -p meld-cli --target wasm32-wasip2 --profile release-wasm
cp ../meld/target/wasm32-wasip2/release-wasm/meld.wasm meld-p2.wasm
python3 run_nowasi_pyc.py meld_nowasi.py                     # or pypy3; expects fused_nowasi.wasm from a wasmtime run to compare
```
