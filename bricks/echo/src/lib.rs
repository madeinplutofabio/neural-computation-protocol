// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! NCP reference echo Brick.
//!
//! - Default: decodes CBOR envelope, extracts `input`, returns Success with output = input.
//! - With `--features trap`: hits `unreachable!()` immediately (for trap-handling tests).
//!
//! **Note:** This brick uses a bump allocator that does not reclaim memory. It is
//! a reference/conformance artifact intended for ABI and spec bring-up, and assumes
//! ephemeral WASM instances (one invocation per instance). Production bricks should
//! use a real allocator (e.g., dlmalloc) for runtimes that reuse instances.

#![no_std]

extern crate alloc;

use alloc::vec::Vec;
use core::slice;

// ── Constants ───────────────────────────────────────────────────────────────

/// ABI alignment for alloc/free. Must be consistent across all call sites.
/// 4-byte alignment ensures the u32 length prefix in result buffers is aligned.
const ABI_ALIGN: usize = 4;

/// Maximum envelope size accepted by invoke(). Matches max_input_bytes in the
/// example manifest (examples/bricks/echo/manifest.template.yaml).
const MAX_ENVELOPE_BYTES: i32 = 65536;

// ── Global allocator ────────────────────────────────────────────────────────

// Simple bump allocator for no_std WASM.
// This is a reference artifact — it does not reclaim memory on dealloc.
// Suitable only for ephemeral WASM instances (one invocation, then drop).
// Production bricks should use a real allocator for instance reuse.

mod allocator {
    use core::alloc::{GlobalAlloc, Layout};
    use core::cell::UnsafeCell;

    const HEAP_SIZE: usize = 262144; // 256 KiB

    pub struct BumpAllocator {
        heap: UnsafeCell<[u8; HEAP_SIZE]>,
        offset: UnsafeCell<usize>,
    }

    unsafe impl Sync for BumpAllocator {}

    impl BumpAllocator {
        pub const fn new() -> Self {
            Self {
                heap: UnsafeCell::new([0u8; HEAP_SIZE]),
                offset: UnsafeCell::new(0),
            }
        }
    }

    unsafe impl GlobalAlloc for BumpAllocator {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let offset = unsafe { &mut *self.offset.get() };
            let align = layout.align();
            let aligned = (*offset + align - 1) & !(align - 1);
            let new_offset = aligned + layout.size();

            if new_offset > HEAP_SIZE {
                return core::ptr::null_mut();
            }

            *offset = new_offset;
            let heap = unsafe { &mut *self.heap.get() };
            heap.as_mut_ptr().add(aligned)
        }

        unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
            // No-op: bump allocator does not reclaim memory.
            // This brick is a reference artifact for ephemeral instances only.
        }
    }
}

#[global_allocator]
static ALLOCATOR: allocator::BumpAllocator = allocator::BumpAllocator::new();

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

// ── CBOR helpers ────────────────────────────────────────────────────────────

/// Minimal envelope parser: extract the raw CBOR bytes of the "input" field.
/// Only accepts definite-length maps (canonical CBOR per §5.2 requires definite-length).
fn extract_input_from_envelope(envelope: &[u8]) -> Option<Vec<u8>> {
    let mut decoder = minicbor::Decoder::new(envelope);

    // Expect a definite-length map
    let len_opt = decoder.map().ok()?;
    let len = match len_opt {
        Some(n) => n,
        None => return None, // reject indefinite-length map
    };

    for _ in 0..len {
        let key = decoder.str().ok()?;
        if key == "input" {
            let start = decoder.position();
            decoder.skip().ok()?;
            let end = decoder.position();
            return Some(envelope[start..end].to_vec());
        } else {
            decoder.skip().ok()?;
        }
    }
    None
}

/// Build a Success result: {"type": "Success", "output": <raw_input_cbor>}
fn build_success_result(input_cbor: &[u8]) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut encoder = minicbor::Encoder::new(&mut buf);

    encoder.map(2).unwrap();
    encoder.str("type").unwrap();
    encoder.str("Success").unwrap();
    encoder.str("output").unwrap();

    // Append raw CBOR value (already encoded)
    buf.extend_from_slice(input_cbor);
    buf
}

/// Build a Failure result for unparseable envelopes.
fn build_failure_result(message: &str) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut encoder = minicbor::Encoder::new(&mut buf);

    encoder.map(2).unwrap();
    encoder.str("type").unwrap();
    encoder.str("Failure").unwrap();
    encoder.str("error").unwrap();
    encoder.map(4).unwrap();
    encoder.str("error_class").unwrap();
    encoder.str("INVALID_INPUT").unwrap();
    encoder.str("message").unwrap();
    encoder.str(message).unwrap();
    encoder.str("retry_advice").unwrap();
    encoder.str("NEVER").unwrap();
    encoder.str("severity").unwrap();
    encoder.str("ERROR").unwrap();

    buf
}

// ── NCP ABI exports ─────────────────────────────────────────────────────────

/// ABI: allocate `len` bytes with ABI_ALIGN alignment. Returns pointer, or 0 on failure.
#[no_mangle]
pub extern "C" fn alloc(len: i32) -> i32 {
    if len <= 0 {
        return 0;
    }
    let layout = match core::alloc::Layout::from_size_align(len as usize, ABI_ALIGN) {
        Ok(l) => l,
        Err(_) => return 0,
    };
    let ptr = unsafe { alloc::alloc::alloc(layout) };
    if ptr.is_null() {
        return 0;
    }
    ptr as i32
}

/// ABI: free `len` bytes at `ptr`. Layout must match alloc (ABI_ALIGN alignment).
#[no_mangle]
pub extern "C" fn free(ptr: i32, len: i32) {
    if ptr == 0 || len <= 0 {
        return;
    }
    let layout = match core::alloc::Layout::from_size_align(len as usize, ABI_ALIGN) {
        Ok(l) => l,
        Err(_) => return,
    };
    unsafe { alloc::alloc::dealloc(ptr as *mut u8, layout) };
}

/// ABI entrypoint: invoke(envelope_ptr, envelope_len) -> result_ptr
///
/// Result buffer format (Section 16.2):
///   - First 4 bytes at result_ptr: result_len as little-endian u32
///   - CBOR result bytes begin at result_ptr + 4
#[no_mangle]
pub extern "C" fn invoke(envelope_ptr: i32, envelope_len: i32) -> i32 {
    // Trap mode: immediately trap for testing Section 16.2.1
    #[cfg(feature = "trap")]
    {
        core::arch::wasm32::unreachable();
    }

    // Guard: null pointer, negative/zero length, or exceeds max_input_bytes
    if envelope_ptr == 0 || envelope_len <= 0 || envelope_len > MAX_ENVELOPE_BYTES {
        return write_result(&build_failure_result("invalid envelope pointer or length"));
    }

    let envelope =
        unsafe { slice::from_raw_parts(envelope_ptr as *const u8, envelope_len as usize) };

    let result_cbor = match extract_input_from_envelope(envelope) {
        Some(input_cbor) => build_success_result(&input_cbor),
        None => build_failure_result("failed to extract input from envelope"),
    };

    write_result(&result_cbor)
}

/// Write CBOR result into a length-prefixed buffer allocated via the exported alloc().
/// Returns result_ptr, or traps on OOM (runtime converts trap to COMPUTATION_ERROR per §16.2.1).
fn write_result(result_cbor: &[u8]) -> i32 {
    let total_len = 4 + result_cbor.len();
    let result_ptr = alloc(total_len as i32);
    if result_ptr == 0 {
        // OOM: trap. Runtime will convert to Failure per Section 16.2.1.
        core::arch::wasm32::unreachable();
    }

    let ptr = result_ptr as *mut u8;
    let len_bytes = (result_cbor.len() as u32).to_le_bytes();
    unsafe {
        core::ptr::copy_nonoverlapping(len_bytes.as_ptr(), ptr, 4);
        core::ptr::copy_nonoverlapping(result_cbor.as_ptr(), ptr.add(4), result_cbor.len());
    }

    result_ptr
}
