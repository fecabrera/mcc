import "std";
import "memory";

// A plain struct. Fields are declared `name: type;`.
struct point {
    x: int32;
    y: int32;
}

// Structs can be generic, and can refer to themselves through a pointer.
struct node<T> {
    value: T;
    next: struct node<T>*;
}

fn sum_list(head: struct node<int32>*) -> int32 {
    let total: int32 = 0;
    let cur = head;
    until (cur == null) {
        total = total + cur->value;
        cur = cur->next;
    }
    return total;
}

fn main() -> int32 {
    // Build a struct value in one go with a struct literal; omitted fields are
    // zero. (See struct_literals.mc for the full tour.)
    let start = struct point { x = 3, y = 4 };
    println("start = (%d, %d)", start.x, start.y);

    // Heap-allocate a struct; `->` reads and writes fields through a pointer.
    let p = alloc<struct point>(1);
    *p = start;             // copy the value in...
    p->y = 4;               // ...or set fields one at a time through the pointer
    println("point = (%d, %d)", p->x, p->y);
    println("sizeof(point) = %llu", sizeof(struct point));

    // Dereferencing copies the struct; `.` accesses fields of a value.
    let q = *p;
    q.x = 30;
    println("q = (%d, %d), p untouched = (%d, %d)", q.x, q.y, p->x, p->y);
    dealloc(p);

    // A linked list of three nodes, terminated by null. Each node's value is
    // written with a struct literal -- generic, with a pointer `next` field.
    let a = alloc<struct node<int32>>(1);
    let b = alloc<struct node<int32>>(1);
    let c = alloc<struct node<int32>>(1);
    // `next` points to a typed node, so the type argument is inferred...
    *a = struct node { value = 1, next = b };
    *b = struct node { value = 2, next = c };
    // ...but here `next` is null (which pins nothing) and `value` is an untyped
    // constant, so there is nothing to infer T from -- spell it out.
    *c = struct node<int32> { value = 4, next = null };
    println("list sum = %d", sum_list(a));
    dealloc(a); dealloc(b); dealloc(c);

    return 0;
}
