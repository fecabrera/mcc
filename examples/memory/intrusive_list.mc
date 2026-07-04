import "std";
import "memory";

// The intrusive-container shape: a struct whose base is a bare type
// parameter, `extends T`. The entry embeds whatever payload it is
// instantiated with as its layout PREFIX and appends its own link after it,
// so an entry is its payload plus a `next` pointer: payload fields are
// reached directly on the entry (e->celsius, not e->payload.celsius), and an
// entry pointer upcasts to the payload with an explicit `as` cast.
//
// Contrast with the hand-built list in types/structs.mc: there `node<T>`
// stores the payload as a named member (`value: T;`), so the payload sits
// behind a wrapper field (n->value). Here the payload IS the front of the
// entry, so there is no wrapper field and no extra indirection.

// The payload: a plain struct that knows nothing about lists.
struct reading {
    celsius: int32;
    station: int32;
}

// The intrusive entry: T's fields first, then the link. `next` must be a
// pointer, the usual self-reference-through-a-pointer rule.
struct entry<T> extends T {
    next: struct entry<T>*;
}

// A payload-only function: it takes a `reading*` and has no idea the value
// lives inside a list entry.
fn describe(r: struct reading*) {
    println("station %d read %d degrees", r->station, r->celsius);
}

// Prepend a heap-allocated entry. The payload fields are set directly on
// the entry -- they are the entry's own fields, laid out at its start.
fn push(head: struct entry<struct reading>*, celsius: int32, station: int32)
        -> struct entry<struct reading>* {
    let e = new<struct entry<struct reading>>();
    e->celsius = celsius;
    e->station = station;
    e->next = head;
    return e;
}

fn main() -> int32 {
    let head: struct entry<struct reading>* = null;
    head = push(head, 21, 1);
    head = push(head, 19, 2);
    head = push(head, 25, 3);

    // Walk the links; hand each payload off through the explicit upcast.
    // The base is a true prefix, so the cast reads the same storage.
    let total: int32 = 0;
    let cur = head;
    until (cur == null) {
        total += cur->celsius;                 // payload field, no indirection
        describe(cur as struct reading*);      // explicit upcast to the payload
        let next = cur->next;
        dealloc(cur);                          // one allocation frees both
        cur = next;
    }
    println("total %d", total);

    return 0;
}

// See also: types/extends.mc for the named-base form of extends, where the
// prefix layout and explicit-upcast rules used here originate;
// types/structs.mc for the non-intrusive list that wraps its payload in a
// named `value` field; pointers.mc for `as` pointer casts.
