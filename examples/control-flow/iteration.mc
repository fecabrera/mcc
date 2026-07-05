import "std";
import "list";
import "set";
import "dict";

// `for x in obj` walks anything that provides the `<struct>_it` / `<struct>_next`
// protocol -- the lib containers in libmc/ all do. The element type is inferred
// from `<struct>_next`, the loop variable is scoped to the loop, and
// `break` / `continue` work as in any loop.

fn main() -> int32 {
    // A growable list (libmc/list.mc) implements list_it / list_next. The
    // container functions take const/mut receivers, so a local passes
    // directly: no & needed. (A list<T>* still works via pointer decay; see
    // examples/functions/pointer_decay.mc.)
    let nums: struct list<int32>;
    list_init(nums, 4);
    defer list_destroy(nums);

    let i: int32 = 1;
    while (i <= 6) {
        list_push(nums, i * i);          // 1 4 9 16 25 36
        i += 1;
    }

    // Iterate the elements. The & is yours to choose, not required -- a struct
    // value is borrowed automatically. break/continue steer the walk.
    let sum: int32 = 0;
    for sq in &nums {
        if (sq % 2 == 0) { continue; }   // skip the even squares
        if (sq > 20) { break; }          // stop past 20
        println("odd square: %d", sq);
        sum += sq;
    }
    println("sum of odd squares <= 20: %d", sum);   // 1 + 9 = 10

    // The same `for x in` walks any container. A set yields a `pair<K, V>` per
    // entry, in unspecified (hash-table) order; read its fields as x.key and
    // x.value. `pair` is a builtin struct, so no import is needed to name it.
    let table: set<uint64, uint64>;
    set_init(table, 2);
    defer set_destroy(table);

    set_set(table, 1, 10);
    set_set(table, 2, 11);
    set_set(table, 3, 12);

    for x in &table {
        println("%llu: %llu", x.key, x.value);
    }

    // A dict iterates the same way: a string key and its value per entry. Each
    // x.key borrows the dict's own copy, valid until the dict changes.
    let cmds: dict<char*>;
    dict_init(cmds, 2);
    defer dict_destroy(cmds);

    dict_set(cmds, "help", "show this help");
    dict_set(cmds, "quit", "exit the program");

    for x in &cmds {
        println("%s: %s", x.key, x.value);
    }

    return 0;
}

// See also: enumerate.mc for the enumerate position counter, ranges.mc for
// `for i in range(...)` counting loops, memory/lists.mc for the list itself.
