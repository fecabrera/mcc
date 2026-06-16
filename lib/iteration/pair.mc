/**
 * A key/value pair -- the element type yielded when iterating a keyed
 * container. The container's `*_next` fills one of these for each occupied
 * entry; see the iteration sections of lib/set.mc and lib/dict.mc.
 *
 * @field key:   the entry's key
 * @field value: the value associated with the key
 */
struct pair<K, V> {
    key: K;
    value: V;
}
