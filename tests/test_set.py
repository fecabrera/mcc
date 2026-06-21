"""lib/set.mc: the open-addressing hash table, checked against Python."""

from pathlib import Path

from helpers import parse, run, run_path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"


def splitmix64(key: int) -> int:
    mask = (1 << 64) - 1
    key ^= key >> 30
    key = (key * 0xBF58476D1CE4E5B9) & mask
    key ^= key >> 27
    key = (key * 0x94D049BB133111EB) & mask
    key ^= key >> 31
    return key


def test_iteration_visits_all_entries():
    # Drives set `next`, instantiating it -- which writes the entry to the out
    # pair via a `set_entry as pair` value upcast (see lib/set.mc).
    assert (
        run(
            """
        import "set";
        import "iteration/pair";
        fn main() -> int32 {
            let s = alloc<struct set<uint64, uint64>>(1);
            set_init(s, 8);
            set_set(s, 10, 100);
            set_set(s, 20, 200);
            set_set(s, 30, 300);
            let it = set_it<uint64, uint64>(s);
            let p: struct pair<uint64, uint64>;
            let total: uint64 = 0;
            while (set_next<uint64, uint64>(&it, &p)) {
                total = total + p.key + p.value;
            }
            return total as int32;
        }
        """
        )
        == 660
    )


def test_for_in_iterates_set():
    # `for x in s` dispatches to set_it/set_next by name.
    assert (
        run(
            """
        import "set";
        fn main() -> int32 {
            let s = alloc<struct set<uint64, uint64>>(1);
            set_init(s, 8);
            set_set(s, 10, 100);
            set_set(s, 20, 200);
            set_set(s, 30, 300);
            let total: uint64 = 0;
            for x in s { total = total + x.key + x.value; }
            return total as int32;
        }
        """
        )
        == 660
    )


def test_nested_generic_type_args_split_shift_token():
    # `list<int32>>` ends with a `>>` token that must close two generics.
    program = parse("fn f(a: struct list<struct list<int32>>*) {}")
    assert str(program.functions[0].params[0][1]) == "list<list<int32>>*"


def test_hash_matches_splitmix64(tmp_path, capfd):
    main = tmp_path / "main.mc"
    main.write_text(
        'import "set";\nimport "libc/stdio";\n'
        'fn main() -> int32 { printf("%llu\\n", splitmix64(12345)); return 0; }'
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == f"{splitmix64(12345)}\n"


def test_set_behaves_like_a_dict(tmp_path, capfd):
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "set";
        import "libc/stdio";
        fn main() -> int32 {
            let s = alloc<struct set<uint64, uint64>>(1);
            set_init(s, 4);

            let i: uint64 = 0;
            while (i < 200) {
                set_set(s, i * 7, i * 1000);   // insert (forces growth)
                i = i + 1;
            }
            i = 0;
            while (i < 100) {
                set_set(s, i * 7, i * 2000);   // update
                i = i + 1;
            }
            i = 0;
            while (i < 200) {
                set_remove(s, i * 7);          // remove every third key
                i = i + 3;
            }

            let errors: uint64 = 0;
            let value: uint64 = 0;
            i = 0;
            while (i < 200) {
                let found = set_get(s, i * 7, &value);
                if (i % 3 == 0) {
                    if (found)
                        errors = errors + 1;
                } else if (!found) {
                    errors = errors + 1;
                } else {
                    let expected: uint64 = i * 1000;
                    if (i < 100)
                        expected = i * 2000;
                    if (value != expected)
                        errors = errors + 1;
                }
                i = i + 1;
            }
            if (set_get(s, 999999, &value))
                errors = errors + 1;           // absent key must not be found

            i = 0;
            while (i < 200) {
                set_set(s, i * 7, 42);         // re-insert into tombstones
                i = i + 3;
            }

            printf("%llu %llu %llu\\n", errors, s->length, s->capacity);
            set_destroy(s);
            dealloc(s);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    errors, length, capacity = capfd.readouterr().out.split()
    assert errors == "0"
    assert length == "200"
    assert int(capacity) >= 512  # grew from 4 while staying under 70% load


def test_generic_keys_and_values(tmp_path, capfd):
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "set";
        import "libc/stdio";
        fn main() -> int32 {
            // int32 keys mapping to float64 values
            let prices = alloc<struct set<int32, float64>>(1);
            set_init(prices, 8);
            set_set(prices, -5, 1.25);
            set_set(prices, 7, 2.5);
            let price: float64 = 0.0;
            if (set_get(prices, -5, &price))
                printf("%f\\n", price);
            set_destroy(prices);
            dealloc(prices);

            // pointer keys (hashed via ptrtoint)
            let names = alloc<struct set<uint8*, int32>>(1);
            set_init(names, 8);
            let hello = "hello";
            set_set(names, hello, 42);
            let found: int32 = 0;
            if (set_get(names, hello, &found))
                printf("%d\\n", found);
            set_destroy(names);
            dealloc(names);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1.250000\n42\n"


def test_fnv1a_matches_reference(tmp_path, capfd):
    def fnv1a(data: bytes) -> int:
        h = 14695981039346656037
        for byte in data:
            h = ((h ^ byte) * 1099511628211) % 2**64
        return h

    main = tmp_path / "main.mc"
    main.write_text(
        'import "hashing/fnv1a";\nimport "libc/stdio";\n'
        'fn main() -> int32 { printf("%llu %llu\\n", fnv1a("hello"), fnv1a("")); return 0; }'
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == f"{fnv1a(b'hello')} {fnv1a(b'')}\n"
