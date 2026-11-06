// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! NCP starter brick: keyword-based ticket classifier.
//!
//! The ONLY file a developer normally edits is this one, and inside it,
//! ONLY the `classify()` function and its tests. Everything else in this
//! file is the WASM ABI boilerplate the runtime needs to call your logic.
//!
//! See the starter README, section "When you change the I/O shape", for
//! what else has to move if you want a different output structure.

// no_std is only needed for the wasm32 target. On the native target we want
// std so `cargo test` can run the unit tests in the #[cfg(test)] module.
#![cfg_attr(target_arch = "wasm32", no_std)]

// ============================================================================
// EDIT ME: business logic. Pure Rust, native-testable with `cargo test`.
// ============================================================================
//
// `classify` is called with the "text" field from the workflow's input.
// Return one of your output categories. The CBOR encoder below wraps the
// returned &str as {"category": <value>} in the Success result. If you
// change the signature or the set of valid return values, see
// "WHEN YOU CHANGE THE I/O SHAPE" in README.md.

pub fn classify(text: &str) -> &'static str {
    let lower = text.to_lowercase();
    if lower.contains("refund") || lower.contains("invoice") || lower.contains("billing") {
        "billing"
    } else if lower.contains("broken") || lower.contains("error") || lower.contains("bug") {
        "support"
    } else {
        "general"
    }
}

#[cfg(test)]
mod tests {
    use super::classify;

    #[test]
    fn billing_keywords_route_to_billing() {
        assert_eq!(classify("I need a refund"), "billing");
        assert_eq!(classify("Invoice question"), "billing");
        assert_eq!(classify("there is a BILLING issue"), "billing");
    }

    #[test]
    fn broken_keywords_route_to_support() {
        assert_eq!(classify("the site is broken"), "support");
        assert_eq!(classify("error 500 on checkout"), "support");
        assert_eq!(classify("found a bug"), "support");
    }

    #[test]
    fn other_text_routes_to_general() {
        assert_eq!(classify("hello there"), "general");
        assert_eq!(classify(""), "general");
    }
}

// ============================================================================
// DO NOT EDIT BELOW: WASM ABI boilerplate.
// ============================================================================
//
// Model B ABI (exports `alloc`, `free`, `invoke`), 4-byte little-endian
// length prefix + CBOR payload in the result buffer, canonical sorted-key
// CBOR map keys. Mirrors the pattern used by bricks/classifier-stub.
// All of this compiles only for wasm32-unknown-unknown.

#[cfg(target_arch = "wasm32")]
extern crate alloc;

#[cfg(target_arch = "wasm32")]
mod wasm_abi {
    use alloc::vec::Vec;
    use core::slice;

    const ABI_ALIGN: usize = 4;
    const MAX_ENVELOPE_BYTES: i32 = 65536;

    // ── Bump allocator (same pattern as the reference brick crates) ────────
    mod allocator {
        use core::alloc::{GlobalAlloc, Layout};
        use core::cell::UnsafeCell;

        const HEAP_SIZE: usize = 262144;

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

            unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {}
        }
    }

    #[global_allocator]
    static ALLOCATOR: allocator::BumpAllocator = allocator::BumpAllocator::new();

    #[panic_handler]
    fn panic(_info: &core::panic::PanicInfo) -> ! {
        core::arch::wasm32::unreachable()
    }

    // ── CBOR envelope decode: extract input.text ───────────────────────────
    fn extract_input_from_envelope(envelope: &[u8]) -> Option<Vec<u8>> {
        let mut decoder = minicbor::Decoder::new(envelope);
        let len = decoder.map().ok()??;
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

    fn extract_text_field<'a>(input_cbor: &'a [u8]) -> Option<&'a str> {
        let mut decoder = minicbor::Decoder::new(input_cbor);
        let len = decoder.map().ok()??;
        for _ in 0..len {
            let key = decoder.str().ok()?;
            if key == "text" {
                return decoder.str().ok();
            } else {
                decoder.skip().ok()?;
            }
        }
        None
    }

    // ── Result builders ────────────────────────────────────────────────────
    // If you change the Success output shape, edit THIS function and the
    // corresponding schemas/output.schema.json. See README "WHEN YOU CHANGE
    // THE I/O SHAPE".
    fn build_success(category: &str) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = minicbor::Encoder::new(&mut buf);
        // {output, type} - 2 keys, sorted ascending
        enc.map(2).unwrap();
        enc.str("output").unwrap();
        // output: {category} - 1 key
        enc.map(1).unwrap();
        enc.str("category").unwrap();
        enc.str(category).unwrap();
        enc.str("type").unwrap();
        enc.str("Success").unwrap();
        buf
    }

    fn build_failure(message: &str) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = minicbor::Encoder::new(&mut buf);
        // {error, type} - 2 keys, sorted ascending
        enc.map(2).unwrap();
        enc.str("error").unwrap();
        // error: {error_class, message} - 2 keys, sorted ascending
        enc.map(2).unwrap();
        enc.str("error_class").unwrap();
        enc.str("INVALID_INPUT").unwrap();
        enc.str("message").unwrap();
        enc.str(message).unwrap();
        enc.str("type").unwrap();
        enc.str("Failure").unwrap();
        buf
    }

    // ── NCP ABI exports ────────────────────────────────────────────────────
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
            0
        } else {
            ptr as i32
        }
    }

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

    #[no_mangle]
    pub extern "C" fn invoke(envelope_ptr: i32, envelope_len: i32) -> i32 {
        if envelope_ptr == 0 || envelope_len <= 0 || envelope_len > MAX_ENVELOPE_BYTES {
            return write_result(&build_failure("invalid envelope pointer or length"));
        }

        let envelope =
            unsafe { slice::from_raw_parts(envelope_ptr as *const u8, envelope_len as usize) };

        let result_cbor = match extract_input_from_envelope(envelope) {
            Some(input_cbor) => match extract_text_field(&input_cbor) {
                Some(text) => build_success(super::classify(text)),
                None => build_failure("input.text field not found or not a string"),
            },
            None => build_failure("failed to extract input from envelope"),
        };

        write_result(&result_cbor)
    }

    fn write_result(result_cbor: &[u8]) -> i32 {
        let total_len = 4 + result_cbor.len();
        let result_ptr = alloc(total_len as i32);
        if result_ptr == 0 {
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
}
