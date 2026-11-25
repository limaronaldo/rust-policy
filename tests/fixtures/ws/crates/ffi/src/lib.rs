//! A C ABI entry point for an ASCII byte buffer.
#![deny(unsafe_op_in_unsafe_fn)]

use core::ffi::c_int;

fn process(input: &[u8]) -> c_int {
    if input.is_ascii() { 0 } else { -2 }
}

/// Processes ASCII bytes: 0 = success, -1 = null/nonempty or oversized input,
/// -2 = non-ASCII content. A panic aborts the process.
/// A zero length permits any pointer, including null; it is not dereferenced.
///
/// # Safety
/// For nonzero `len` with a non-null `data` and `len <= isize::MAX`,
/// `data` must point to `len` initialized consecutive `u8` values in one
/// allocation, with provenance permitting reads of the entire range.
/// The range must not wrap the address space and must remain readable and
/// unmodified by any thread or foreign code until this call returns.
/// `data` must be aligned for `u8` (alignment 1). Ownership stays with the caller.
// SAFETY: the integrating program must reserve this exact global symbol;
// no other definition may use it, and callers must use this C ABI signature.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn demo_rust_process(data: *const u8, len: usize) -> c_int {
    if len > isize::MAX as usize || (len != 0 && data.is_null()) {
        return -1;
    }

    match std::panic::catch_unwind(|| {
        let input: &[u8] = if len == 0 {
            &[] // Never construct a slice from a possibly null pointer.
        } else {
            // SAFETY: checks establish non-nullness and the size bound;
            // the caller supplies the remaining documented guarantees.
            unsafe { core::slice::from_raw_parts(data, len) }
        };
        process(input)
    }) {
        Ok(status) => status,
        // abort() does not return, allocate here, or drop the panic payload.
        Err(_payload) => std::process::abort(),
    }
}

#[cfg(test)]
mod tests {
    use super::demo_rust_process;

    #[test]
    fn accepts_ascii_bytes() {
        let bytes = b"Rust policy";
        // SAFETY: this live array is initialized and readable for its full length.
        let result = unsafe { demo_rust_process(bytes.as_ptr(), bytes.len()) };
        assert_eq!(result, 0);
    }

    #[test]
    fn rejects_non_ascii_bytes() {
        let bytes = [0xff_u8];
        // SAFETY: this live array is initialized and readable for its full length.
        let result = unsafe { demo_rust_process(bytes.as_ptr(), bytes.len()) };
        assert_eq!(result, -2);
    }

    #[test]
    fn accepts_null_empty_buffer() {
        // SAFETY: the documented zero-length case does not dereference the pointer.
        let result = unsafe { demo_rust_process(core::ptr::null(), 0) };
        assert_eq!(result, 0);
    }

    #[test]
    fn rejects_null_nonempty_buffer() {
        // SAFETY: the documented null-pointer case returns an error before any read.
        let result = unsafe { demo_rust_process(core::ptr::null(), 1) };
        assert_eq!(result, -1);
    }

    #[test]
    fn rejects_oversized_buffer() {
        let byte = 0_u8;
        // SAFETY: an oversized length is rejected before the pointer is dereferenced.
        let result = unsafe { demo_rust_process(&byte, isize::MAX as usize + 1) };
        assert_eq!(result, -1);
    }
}
