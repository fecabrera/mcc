@extern fn malloc(size: uint64) -> uint8*;
@extern fn free(ptr: uint8*);
@extern fn memcpy(dest: uint8*, source: uint8*, count: uint64) -> uint8*;

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
 */
fn copy_bytes<T>(dst: T*, src: T*, n: uint64) {
    memcpy(dst, src, n * sizeof(T));
}

/**
 * Copies n elements of type T from src to dst one item at a time.
 *
 * @param dst: destination, with room for at least n elements
 * @param src: source to read from
 * @param n:   number of elements to copy
 */
fn copy_items<T>(dst: T*, src: T*, n: uint64) {
    let i: uint64 = 0;
    while (i < n) {
        dst[i] = src[i];
        i = i + 1;
    }
}
