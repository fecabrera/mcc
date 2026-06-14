import "libc/stdio";
import "array";

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
    // Heap-allocate a struct; `->` reads and writes fields through a pointer.
    let p = alloc<struct point>(1);
    p->x = 3;
    p->y = 4;
    printf("point = (%d, %d)\n", p->x, p->y);
    printf("sizeof(point) = %llu\n", sizeof(struct point));

    // Dereferencing copies the struct; `.` accesses fields of a value.
    let q = *p;
    q.x = 30;
    printf("q = (%d, %d), p untouched = (%d, %d)\n", q.x, q.y, p->x, p->y);
    dealloc(p);

    // A linked list of three nodes, terminated by null.
    let a = alloc<struct node<int32>>(1);
    let b = alloc<struct node<int32>>(1);
    let c = alloc<struct node<int32>>(1);
    a->value = 1;  a->next = b;
    b->value = 2;  b->next = c;
    c->value = 4;  c->next = null;
    printf("list sum = %d\n", sum_list(a));
    dealloc(a); dealloc(b); dealloc(c);

    // The growable array<T> from lib/array.mc.
    let nums = alloc<struct array<int32>>(1);
    array_init(nums, 2);
    let i: int32 = 0;
    while (i < 10) {
        array_append(nums, i * i);
        i = i + 1;
    }
    printf("array length = %llu, capacity = %llu\n", nums->length, nums->capacity);
    let value: int32 = 0;
    if (array_get(nums, 6, &value))
        printf("nums[6] = %d\n", value);
    array_destroy(nums);
    dealloc(nums);

    return 0;
}
