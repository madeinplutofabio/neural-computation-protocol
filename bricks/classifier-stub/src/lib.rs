// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! NCP stub classifier brick for benchmarks.
//!
//! Keyword-based sentiment routing:
//! - If `input.text` contains a negative keyword → LowConfidence (confidence=0.30)
//!   → runtime routes via on_error(LOW_CONFIDENCE) to escalation node
//! - Otherwise → Success (confidence=0.95) → terminal
//!
//! This is a benchmark artifact, not a real classifier.

#![no_std]

extern crate alloc;

use alloc::vec::Vec;
use core::slice;

const ABI_ALIGN: usize = 4;
const MAX_ENVELOPE_BYTES: i32 = 65536;

// Negative keywords that trigger LowConfidence → escalation
const NEGATIVE_KEYWORDS: &[&[u8]] = &[
    b"angry",
    b"frustrated",
    b"unacceptable",
    b"terrible",
    b"horrible",
    b"worst",
    b"refund",
    b"escalate",
    b"complaint",
    b"lawsuit",
];

// ── Allocator (same bump allocator as echo brick) ───────────────────────────

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

// ── CBOR helpers ────────────────────────────────────────────────────────────

/// Extract the raw CBOR bytes of the "input" field from the envelope.
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

/// Extract the text string value of "text" from a CBOR map.
fn extract_text_field(input_cbor: &[u8]) -> Option<Vec<u8>> {
    let mut decoder = minicbor::Decoder::new(input_cbor);
    let len = decoder.map().ok()??;
    for _ in 0..len {
        let key = decoder.str().ok()?;
        if key == "text" {
            let val = decoder.str().ok()?;
            return Some(val.as_bytes().to_vec());
        } else {
            decoder.skip().ok()?;
        }
    }
    None
}

/// Case-insensitive substring search: does `haystack` contain `needle`?
fn contains_ci(haystack: &[u8], needle: &[u8]) -> bool {
    if needle.len() > haystack.len() {
        return false;
    }
    for i in 0..=(haystack.len() - needle.len()) {
        let mut found = true;
        for j in 0..needle.len() {
            if to_lower(haystack[i + j]) != to_lower(needle[j]) {
                found = false;
                break;
            }
        }
        if found {
            return true;
        }
    }
    false
}

// Rationale: ASCII range check via `>=`/`<=` compiles to simpler wasm
// than `RangeInclusive::contains` (no range allocation) and is more
// idiomatic for byte-level ops in no_std. Source unchanged keeps the
// wasm artifact + manifest digest valid.
#[allow(clippy::manual_range_contains)]
fn to_lower(b: u8) -> u8 {
    if b >= b'A' && b <= b'Z' {
        b + 32
    } else {
        b
    }
}

/// Check if text contains any negative keyword.
fn is_negative(text: &[u8]) -> bool {
    for keyword in NEGATIVE_KEYWORDS {
        if contains_ci(text, keyword) {
            return true;
        }
    }
    false
}

// ── Result builders ─────────────────────────────────────────────────────────

/// Build Success result: {type: "Success", output: {label: <label>, confidence: <conf>}}
fn build_success(label: &str, confidence: f64) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = minicbor::Encoder::new(&mut buf);
    // {output, type} — 2 keys, sorted
    enc.map(2).unwrap();
    enc.str("output").unwrap();
    // output map: {confidence, label} — 2 keys, sorted
    enc.map(2).unwrap();
    enc.str("confidence").unwrap();
    enc.f64(confidence).unwrap();
    enc.str("label").unwrap();
    enc.str(label).unwrap();
    enc.str("type").unwrap();
    enc.str("Success").unwrap();
    buf
}

/// Build LowConfidence result:
/// {type: "LowConfidence", output: {label, confidence}, error: {error_class, message}}
fn build_low_confidence(label: &str, confidence: f64) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = minicbor::Encoder::new(&mut buf);
    // {error, output, type} — 3 keys, sorted
    enc.map(3).unwrap();
    enc.str("error").unwrap();
    // error map: {error_class, message} — 2 keys, sorted
    enc.map(2).unwrap();
    enc.str("error_class").unwrap();
    enc.str("LOW_CONFIDENCE").unwrap();
    enc.str("message").unwrap();
    enc.str("negative keyword detected").unwrap();
    enc.str("output").unwrap();
    // output map: {confidence, label} — 2 keys, sorted
    enc.map(2).unwrap();
    enc.str("confidence").unwrap();
    enc.f64(confidence).unwrap();
    enc.str("label").unwrap();
    enc.str(label).unwrap();
    enc.str("type").unwrap();
    enc.str("LowConfidence").unwrap();
    buf
}

/// Build Failure result for bad input.
fn build_failure(message: &str) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = minicbor::Encoder::new(&mut buf);
    enc.map(2).unwrap();
    enc.str("error").unwrap();
    enc.map(2).unwrap();
    enc.str("error_class").unwrap();
    enc.str("INVALID_INPUT").unwrap();
    enc.str("message").unwrap();
    enc.str(message).unwrap();
    enc.str("type").unwrap();
    enc.str("Failure").unwrap();
    buf
}

// ── NCP ABI exports ─────────────────────────────────────────────────────────

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
            Some(text) => {
                if is_negative(&text) {
                    build_low_confidence("negative", 0.30)
                } else {
                    build_success("positive", 0.95)
                }
            }
            None => build_failure("input.text field not found"),
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
