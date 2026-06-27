@extern fn malloc(size: uint64) -> uint8*;
@extern fn realloc(ptr: uint8*, size: uint64) -> uint8*;
@extern fn free(ptr: uint8*);
@extern fn memcpy(dest: uint8*, source: uint8*, count: uint64) -> uint8*;
@extern fn memset(dest: uint8*, ch: int32, count: uint64) -> uint8*;

/**
 * Allocates heap space for n elements of type T.
 *
 * @param n: number of elements to allocate space for
 *
 * @return pointer to the first element; the memory is uninitialized
 */
fn alloc<T>(n: uint64) -> T* {
    return malloc(n * sizeof(T)) as T*;
}

/**
 * Resizes a block previously returned by alloc<T> to hold n elements of type
 * T, preserving its contents up to the smaller of the old and new sizes.
 * Returns the (possibly relocated) pointer; the old pointer must not be used
 * afterward. Passing null allocates a fresh block.
 *
 * @param p: pointer returned by alloc<T>, or null
 * @param n: new number of elements
 *
 * @return pointer to the resized block
 */
fn resize<T>(p: T*, n: uint64) -> T* {
    return realloc(p, n * sizeof(T)) as T*;
}

/**
 * Releases memory previously returned by alloc.
 *
 * @param p: pointer returned by alloc<T>; null is allowed and does nothing
 */
fn dealloc<T>(p: T*) {
    free(p);
}

/**
 * Byte-copies n elements of type T from src to dst in a single memcpy.
 * The element size is computed from T, so callers count elements, not
 * bytes. The regions must not overlap.
 *
 * @param dst: destination, with room for at least n elements
 * @param src: source to read from
 * @param n:   number of elements to copy
 * @return number of bytes copied (n * sizeof(T))
 */
@inline
fn bytecopy<T>(dst: T*, src: T*, n: uint64) -> uint64 {
    let count = n * sizeof(T);
    memcpy(dst, src, count);
    return count;
}

// deprecated
@inline
fn copy_bytes<T>(dst: T*, src: T*, n: uint64) {
    bytecopy(dst, src, n);
}

/**
 * Copies n elements of type T from src to dst one item at a time.
 *
 * @param dst: destination, with room for at least n elements
 * @param src: source to read from
 * @param n:   number of elements to copy
 * @return number of elements copied (n)
 */
fn copy<T>(dst: T*, src: T*, n: uint64) -> uint64 {
    let i: uint64 = 0;
    while (i < n) {
        dst[i] = src[i];
        i = i + 1;
    }
    return i;
}

// deprecated
@inline
fn copy_items<T>(dst: T*, src: T*, n: uint64) {
    copy(dst, src, n);
}

/**
 * Zeroes the n elements of type T at dst by clearing every byte, in a single
 * memset. The element size is computed from T, so callers count elements, not
 * bytes. Shorthand for set_bytes(dst, 0, n).
 *
 * @param dst: destination, with room for at least n elements
 * @param n:   number of elements to zero
 */
fn bytezero<T>(dst: T*, n: uint64) {
    set_bytes(dst, 0, n);
}

/**
 * Zeroes the n elements of type T at dst one item at a time, writing a whole
 * zero-valued T to each element rather than a byte pattern. Shorthand for
 * set_items(dst, 0, n).
 *
 * @param dst: destination, with room for at least n elements
 * @param n:   number of elements to zero
 */
fn zero<T>(dst: T*, n: uint64) {
    set_items(dst, 0, n);
}

/**
 * Fills the n elements of type T at dst with the byte `value`, in a single
 * memset. The element size is computed from T, so callers count elements,
 * not bytes; pass 0 to zero the region.
 *
 * @param dst:   destination, with room for at least n elements
 * @param value: the byte written to every byte of the region
 * @param n:     number of elements to fill
 */
fn set_bytes<T>(dst: T*, value: uint8, n: uint64) {
    memset(dst, value as int32, n * sizeof(T));   // libc memset takes an int
}

/**
 * Sets the n elements of type T at dst to `value`, one item at a time -- so
 * it writes whole T values, not just a repeated byte pattern.
 *
 * @param dst:   destination, with room for at least n elements
 * @param value: the value written to each element
 * @param n:     number of elements to set
 */
fn set_items<T>(dst: T*, value: T, n: uint64) {
    let i: uint64 = 0;
    while (i < n) {
        dst[i] = value;
        i = i + 1;
    }
}
