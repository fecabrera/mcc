/***************************************
 * errno
 ***************************************/

// In C, `errno` is a macro that expands to a modifiable lvalue, implemented as
// the dereference of a per-thread location returned by a libc function. The
// function differs by platform -- __error() on macOS/BSD, __errno_location() on
// glibc -- so @if + @symbol bind it behind one name, and the getter/setter
// below stand in for C's `errno` lvalue.

@if (TARGET_OS == OS_DARWIN) {
    @extern @symbol("__error") fn errno_location() -> int32*;
} @else {
    @extern @symbol("__errno_location") fn errno_location() -> int32*;
}

/**
 * Reads the current thread's errno value.
 *
 * @return the current errno
 */
fn errno() -> int32 {
    return *errno_location();
}

/**
 * Sets the current thread's errno value. Pass 0 to clear it before a call whose
 * failure you intend to detect via errno.
 *
 * @param value: the new errno value
 */
fn set_errno(value: int32) {
    *errno_location() = value;
}

/***************************************
 * Error codes
 ***************************************/

// The three error codes the C standard defines. EDOM and ERANGE share the same
// value on macOS and Linux; EILSEQ does not.
const EDOM = 33;     // argument outside a function's domain
const ERANGE = 34;   // result outside the representable range

@if (TARGET_OS == OS_DARWIN) {
    const EILSEQ = 92;   // illegal byte sequence
} @else {
    const EILSEQ = 84;
}
