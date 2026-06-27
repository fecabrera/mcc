// A half-open integer range [start, end) and its iterator, for counting loops
// with `for ... in`. Generic over the integer type T.

struct range<T> {
    start: T = 0;   // first value, included
    end: T;     // one past the last value, excluded
}

struct range_iter<T> {
    obj: struct range<T>*;
    idx: T;
}

/**
 * Begins an iteration over the half-open range [start, end), in ascending
 * order. Part of the `range_it`/`range_next` protocol (used by `for ... in`);
 * pair it with `range_next`.
 *
 * @param self: range to iterate
 *
 * @return an iterator positioned at start
 */
@inline
fn range_it<T>(self: struct range<T>*) -> struct range_iter<T> {
    let it: struct range_iter<T>;
    it.obj = self;
    it.idx = self->start;
    return it;
}

/**
 * Advances to the next value in the range and writes it into out.
 *
 * @param it:  iterator to advance
 * @param out: the next value, written when one remains; untouched once the
 *             range is exhausted
 *
 * @return true if a value was produced, false once idx reaches end
 */
@inline
fn range_next<T>(it: struct range_iter<T>*, out: T*) -> bool {
    if (it->idx < it->obj->end) {
        *out = it->idx;
        it->idx = it->idx + 1;
        return true;
    }

    return false;
}
