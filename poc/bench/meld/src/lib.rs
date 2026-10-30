//! meld-core as a pure "bytes in, bytes out" wasm module: no WASI, no imports.
//! Host protocol: p = meld_alloc(n); write the component at p; rc = meld_fuse(p, n);
//! rc == 0 -> fused core module at meld_out_ptr(), meld_out_len() bytes; rc == 1 -> error text there.
use meld_core::{Fuser, FuserConfig};

static mut OUT: Vec<u8> = Vec::new();

#[unsafe(no_mangle)]
pub extern "C" fn meld_alloc(len: usize) -> *mut u8 {
    let mut v = Vec::<u8>::with_capacity(len);
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn meld_fuse(ptr: *mut u8, len: usize) -> i32 {
    let input = unsafe { Vec::from_raw_parts(ptr, len, len) };
    // reproducible attestation: no wall clock (SystemTime is unavailable without WASI)
    let config = FuserConfig { reproducible: true, ..FuserConfig::default() };
    let mut f = Fuser::new(config);
    let r = f.add_component(&input).and_then(|_| f.fuse());
    let (rc, out) = match r {
        Ok(bytes) => (0, bytes),
        Err(e) => (1, format!("{e:#}").into_bytes()),
    };
    unsafe { OUT = out; }
    rc
}

#[unsafe(no_mangle)]
pub extern "C" fn meld_out_ptr() -> *const u8 { unsafe { (*std::ptr::addr_of!(OUT)).as_ptr() } }

#[unsafe(no_mangle)]
pub extern "C" fn meld_out_len() -> usize { unsafe { (*std::ptr::addr_of!(OUT)).len() } }
