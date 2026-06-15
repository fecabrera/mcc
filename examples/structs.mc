import "std";
import "array";
import "stack";

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
    println("point = (%d, %d)", p->x, p->y);
    println("sizeof(point) = %llu", sizeof(struct point));

    // Dereferencing copies the struct; `.` accesses fields of a value.
    let q = *p;
    q.x = 30;
    println("q = (%d, %d), p untouched = (%d, %d)", q.x, q.y, p->x, p->y);
    dealloc(p);

    // A linked list of three nodes, terminated by null.
    let a = alloc<struct node<int32>>(1);
    let b = alloc<struct node<int32>>(1);
    let c = alloc<struct node<int32>>(1);
    a->value = 1;  a->next = b;
    b->value = 2;  b->next = c;
    c->value = 4;  c->next = null;
    println("list sum = %d", sum_list(a));
    dealloc(a); dealloc(b); dealloc(c);

    // The growable array<T> from lib/array.mc.
    let nums = alloc<struct array<int32>>(1);
    array_init(nums, 2);
    let i: int32 = 0;
    while (i < 10) {
        array_append(nums, i * i);
        i = i + 1;
    }
    println("array length = %llu, capacity = %llu", nums->length, nums->capacity);
    let value: int32 = 0;
    if (array_get(nums, 6, &value))
        println("nums[6] = %d", value);
    array_destroy(nums);
    dealloc(nums);

    // The growable LIFO stack<T> from lib/stack.mc.
    let chars: struct stack<uint8>;
    stack_init(&chars, 2);
    stack_push(&chars, 'a');
    stack_push(&chars, 'b');
    stack_push(&chars, 'c');             // grows past the initial capacity of 2
    println("stack: len %llu, top %c", stack_len(&chars), stack_peek(&chars));
    print("popping: ");
    until (stack_is_empty(&chars)) {
        print("%c ", stack_pop(&chars)); // LIFO: c b a
    }
    println("");
    stack_destroy(&chars);

    return 0;
}
