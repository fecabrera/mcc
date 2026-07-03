import "libc/string";
import "libc/stdlib";

/**
 * Allocates heap space for n elements of type T.
 *
 * @param n: number of elements to allocate space for
 *
 * @return pointer to the first element; the memory is uninitialized
 */
@inline
fn alloc<T>(n: uint64) -> T* {
    return malloc(n * sizeof(T)) as T*;
}

/**
 * Allocates heap space for a single element of type T -- shorthand for
 * alloc<T>(1).
 *
 * @return pointer to the element; the memory is uninitialized
 */
@inline
fn new<T>() -> T* {
    return alloc<T>(1);
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
@inline
fn resize<T>(p: T*, n: uint64) -> T* {
    return realloc(p, n * sizeof(T)) as T*;
}

/**
 * Releases memory previously returned by alloc.
 *
 * @param p: pointer returned by alloc<T>; null is allowed and does nothing
 */
@inline
fn dealloc<T>(p: T*) {
    free(p);
}

/**
 * Byte-copies n elements of type T from src to dst in a single memcpy.
 * The element size is computed from T, so callers count elements, not
 * bytes. dst and src are @noalias: the regions must not overlap, or the
 * behavior is undefined (use a manual loop if they might).
 *
 * @param dst: destination, with room for at least n elements; must not
 *             overlap src
 * @param src: source to read from
 * @param n:   number of elements to copy
 *
 * @return number of bytes copied (n * sizeof(T))
 */
@inline
fn bytecopy<T>(@noalias dst: T*, @noalias src: T*, n: uint64) -> uint64 {
    let count = n * sizeof(T);
    memcpy(dst, src, count);
    return count;
}

@deprecated("use bytecopy instead")
@inline
fn copy_bytes<T>(dst: T*, src: T*, n: uint64) {
    bytecopy(dst, src, n);
}

/**
 * Copies n elements of type T from src to dst one item at a time. dst and src
 * are @noalias: the regions must not overlap, or the behavior is undefined
 * (use a manual loop if they might).
 *
 * @param dst: destination, with room for at least n elements; must not
 *             overlap src
 * @param src: source to read from
 * @param n:   number of elements to copy
 *
 * @return number of elements copied (n)
 */
@inline
fn copy<T>(@noalias dst: T*, @noalias src: T*, n: uint64) -> uint64 {
    for i in range(n) {
        dst[i] = src[i];
    }
    return n;
}

@deprecated("use copy instead")
@inline
fn copy_items<T>(dst: T*, src: T*, n: uint64) {
    copy(dst, src, n);
}

/**
 * Zeroes the n elements of type T at dst by clearing every byte, in a single
 * memset. The element size is computed from T, so callers count elements, not
 * bytes. Shorthand for bytefill(dst, 0, n).
 *
 * @param dst: destination, with room for at least n elements
 * @param n:   number of elements to zero
 *
 * @return number of bytes zeroed (n * sizeof(T))
 */
@inline
fn bytezero<T>(dst: T*, n: uint64) -> uint64 {
    return bytefill(dst, 0, n);
}

/**
 * Zeroes the n elements of type T at dst one item at a time, writing a whole
 * zero-valued T to each element rather than a byte pattern. Shorthand for
 * fill(dst, 0, n).
 *
 * @param dst: destination, with room for at least n elements
 * @param n:   number of elements to zero
 *
 * @return number of elements zeroed (n)
 */
@inline
fn zero<T>(dst: T*, n: uint64) -> uint64 {
    return fill(dst, 0, n);
}

/**
 * Fills the n elements of type T at dst with the byte `value`, in a single
 * memset. The element size is computed from T, so callers count elements,
 * not bytes; pass 0 to zero the region.
 *
 * @param dst:   destination, with room for at least n elements
 * @param value: the byte written to every byte of the region
 * @param n:     number of elements to fill
 *
 * @return number of bytes filled (n * sizeof(T))
 */
@inline
fn bytefill<T>(dst: T*, value: byte, n: uint64) -> uint64 {
    let count = n * sizeof(T);
    memset(dst, value as int32, count);   // libc memset takes an int
    return count;
}

@deprecated("use bytefill instead")
@inline
fn set_bytes<T>(dst: T*, value: byte, n: uint64) {
    bytefill(dst, value, n);
}

/**
 * Sets the n elements of type T at dst to `value`, one item at a time -- so
 * it writes whole T values, not just a repeated byte pattern.
 *
 * @param dst:   destination, with room for at least n elements
 * @param value: the value written to each element
 * @param n:     number of elements to fill
 *
 * @return number of elements filled (n)
 */
@inline
fn fill<T>(dst: T*, value: T, n: uint64) -> uint64 {
    for i in range(n) {
        dst[i] = value;
    }
    return n;
}

@deprecated("use fill instead")
@inline
fn set_items<T>(dst: T*, value: T, n: uint64) {
    fill(dst, value, n);
}
