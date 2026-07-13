import "std/io";

// A generic struct extending a generic base built from its own parameters:
// `struct entry<K, V> extends cell<K, V>`. Base and extender are
// monomorphized TOGETHER, one concrete layout per instantiation, with the
// base's fields first as for any named base.
// Prerequisites: extends.mc; structs.mc for generic structs.

// The base: a generic key/value cell.
struct cell<K, V> {
    key:   K;
    value: V;
}

// The extender passes its own parameters through to the base, then appends
// its bookkeeping: entry<int32, uint8*> is laid out key, value, state.
struct entry<K, V> extends cell<K, V> {
    state: uint8;                 // 0 = empty, 1 = occupied
}

// A function over one base instantiation; the matching entry upcasts to it.
fn describe(c: struct cell<int32, uint8*>*) {
    println("{} -> {}", c!->key, c!->value as char*);
}

fn main() -> int32 {
    // One caveat on literals: type-argument inference walks only the
    // extender's OWN fields, so a literal naming base fields needs explicit
    // type arguments. Without them, `entry { key = 3, ... }` fails with
    // "struct 'entry' has no field 'key'": key is nobody's field until
    // K and V are bound.
    let e = entry<int32, uint8*> { key = 3, value = "three", state = 1 };
    println("key {}, value {}, state {}", e.key, e.value as char*, e.state);

    // The pointer upcast works per instantiation, to the matching base.
    describe(&e as struct cell<int32, uint8*>*);

    // A second instantiation is a separate monomorphization with its own
    // layout; the two entry types never interconvert.
    let f = entry<int32, int32> { key = 7, value = 49, state = 1 };
    println("f: {} -> {}, state {}", f.key, f.value, f.state);

    // Each entry is its cell plus the state byte, padded to alignment.
    println("sizeof entry<int32, uint8*> = {} (cell {})",
            sizeof(struct entry<int32, uint8*>),
            sizeof(struct cell<int32, uint8*>));
    println("sizeof entry<int32, int32> = {} (cell {})",
            sizeof(struct entry<int32, int32>),
            sizeof(struct cell<int32, int32>));

    return 0;
}

// See also: extends.mc for the named-base fundamentals (prefix layout,
// explicit upcasts, defaults); method_inheritance.mc for the base's method
// families riding the lineage, a generic derivation's staying generic
// included; memory/intrusive_list.mc for the bare-parameter base
// `extends T`.
