/**
 * Exchanges two values in place.
 *
 * Both parameters are `mut`, so the swap happens in the caller's storage
 * with no pointers at the call site: `swap(x, y);`.
 *
 * @param a: first value
 * @param b: second value
 */
@inline
fn swap<T>(mut a: T, mut b: T) {
    let t = a;
    a = b;
    b = t;
}

/**
 * Stores value into dst and returns the previous value of dst.
 *
 * @param dst:   destination, updated in the caller's storage
 * @param value: value to store
 *
 * @return the value dst held before the call
 */
@inline
fn replace<T>(mut dst: T, value: T) -> T {
    let old = dst;
    dst = value;
    return old;
}
