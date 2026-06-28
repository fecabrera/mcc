import "std";
import "list";
import "set";
import "dict";

// `for x in obj` walks anything that provides the `<struct>_it`/`<struct>_next`
// protocol -- here the growable list from libmc/list.mc (list_it/list_next).
// The element type is inferred from `<struct>_next`, the loop variable `x` is
// scoped to the loop, and break/continue work as in any loop.

fn main() -> int32 {
    let nums: struct list<int32>;
    list_init(&nums, 4);
    defer list_destroy(&nums);          // freed however main exits

    let i: int32 = 1;
    while (i <= 6) {
        list_push(&nums, i * i);      // 1 4 9 16 25 36
        i = i + 1;
    }

    // Sum the squares, stopping once they exceed 20.
    let sum: int32 = 0;
    for sq in &nums {
        if (sq % 2 == 0) { continue; }   // skip the even squares
        if (sq > 20) { break; }          // and stop past 20
        println("odd square: %d", sq);
        sum = sum + sq;
    }
    println("sum of odd squares <= 20: %d", sum);   // 1 + 9 = 10

    // A bare { } block is its own scope -- a place for a short-lived helper
    // and its cleanup, without leaking names into the rest of the function.
    {
        let scratch: uint8* = alloc<uint8>(8);
        defer dealloc(scratch);
        scratch[0] = 'h';
        scratch[1] = 'i';
        scratch[2] = 0;
        println("%s", scratch);
    }   // scratch is freed and out of scope here

    // The other lib containers implement the same protocol. Iterating a set
    // yields a `pair<K, V>` per entry, in unspecified (hash-table) order; read
    // its fields as x.key and x.value.
    let table: set<uint64, uint64>;
    set_init(&table, 2);
    defer set_destroy(&table);

    set_set(&table, 1, 10);
    set_set(&table, 2, 11);
    set_set(&table, 3, 12);

    for x in &table {
        println("%llu: %llu", x.key, x.value);
    }

    // A dict iterates the same way: a string key and the value, per entry.
    // Each x.key borrows the dict's own copy of the key, valid until the dict
    // changes.
    let cmds: dict<uint8*>;
    dict_init(&cmds, 2);
    defer dict_destroy(&cmds);

    dict_set(&cmds, "help", "show this help");
    dict_set(&cmds, "quit", "exit the program");
    dict_set(&cmds, "nuke", "nuke something or idk");

    for x in &cmds {
        println("%s: %s", x.key, x.value);
    }

    return 0;
}
