/**
 * A forward cursor over a container of type T, the shared shape behind the
 * `_it`/`_next` iteration protocol. It holds a borrowed pointer to the
 * container and the index of the next slot to yield, so the container must
 * outlive the iterator and must not be resized while iterating. Each container
 * (`list`, `set`, `dict`, `string`) returns one of these from its `_it` and
 * advances it in its `_next`, rather than defining its own cursor struct.
 */
struct iterator<T> {
    obj: T*;        // the container being walked
    idx: uint64;    // index of the next element to yield
}
