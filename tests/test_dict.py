"""lib/dict.mc: the owning, content-keyed string map."""

from helpers import run, run_path


def test_iteration_visits_all_entries():
    # Drives dict `next`, instantiating it -- which writes the entry to the out
    # pair via a `dict_entry as pair` value upcast (see lib/dict.mc).
    assert run(
        """
        import "dict";
        fn main() -> int32 {
            let d = alloc<struct dict<uint64>>(1);
            dict_init(d, 8);
            dict_set(d, "a", 10);
            dict_set(d, "b", 20);
            dict_set(d, "c", 30);
            let it = dict_it<uint64>(d);
            let p: struct pair<char*, uint64>;
            let total: uint64 = 0;
            while (dict_next<uint64>(&it, &p)) { total = total + p.value; }
            return total as int32;
        }
        """
    ) == 60


def test_for_in_iterates_dict():
    # `for x in &d` dispatches to dict_it/dict_next by name.
    assert run(
        """
        import "dict";
        fn main() -> int32 {
            let d = alloc<struct dict<uint64>>(1);
            dict_init(d, 8);
            dict_set(d, "a", 10);
            dict_set(d, "b", 20);
            dict_set(d, "c", 30);
            let total: uint64 = 0;
            for x in d { total = total + x.value; }
            return total as int32;
        }
        """
    ) == 60


def test_lookup_by_content_not_address(tmp_path, capfd):
    # Each string literal occurrence is a distinct global, so these two
    # "hello"s are different pointers; only content matching can find it.
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "dict";
        import "libc/stdio";
        fn main() -> int32 {
            let d = alloc<struct dict<int32>>(1);
            dict_init(d, 4);
            dict_set(d, "hello", 42);
            let v: int32 = 0;
            if (dict_get(d, "hello", v))
                printf("found %d\\n", v);
            dict_set(d, "hello", 43);          // update via another pointer
            dict_get(d, "hello", v);
            printf("updated %d, length %llu\\n", v, d->length);
            if (!dict_get(d, "absent", v))
                puts("absent missing");
            dict_destroy(d);
            dealloc(d);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "found 42\nupdated 43, length 1\nabsent missing\n"


def test_dict_owns_key_copies(tmp_path, capfd):
    # One scratch buffer, rewritten for every insert: entries can only stay
    # distinct if dict_set copied the key.
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "dict";
        import "libc/stdio";
        fn main() -> int32 {
            let d = alloc<struct dict<int32>>(1);
            dict_init(d, 2);

            let scratch = alloc<char>(3);
            if (scratch == null) { return 1; }  // narrows scratch, loops keep it
            let i: int32 = 0;
            while (i < 100) {
                scratch[0] = (65 + i / 10) as char;
                scratch[1] = (65 + i % 10) as char;
                scratch[2] = 0;
                dict_set(d, scratch, i * 7);    // heap key into @nonnull, in-loop
                i = i + 1;
            }
            scratch[0] = 90;  // clobber the caller's buffer
            scratch[1] = 0;

            let errors: int32 = 0;
            let v: int32 = 0;
            i = 0;
            while (i < 100) {
                scratch[0] = (65 + i / 10) as char;
                scratch[1] = (65 + i % 10) as char;
                scratch[2] = 0;
                if (!dict_get(d, scratch, v))
                    errors = errors + 1;
                else if (v != i * 7)
                    errors = errors + 1;
                i = i + 1;
            }
            printf("%d %llu %llu\\n", errors, d->length, d->capacity);
            dealloc(scratch);
            dict_destroy(d);
            dealloc(d);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    errors, length, capacity = capfd.readouterr().out.split()
    assert errors == "0"
    assert length == "100"
    assert int(capacity) >= 128  # grew from 2


def test_remove_and_tombstone_reuse(tmp_path, capfd):
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "dict";
        import "libc/stdio";
        fn main() -> int32 {
            let d = alloc<struct dict<int32>>(1);
            dict_init(d, 8);
            dict_set(d, "alpha", 1);
            dict_set(d, "beta", 2);
            dict_remove(d, "alpha");
            let v: int32 = 0;
            let gone = !dict_get(d, "alpha", v);
            dict_set(d, "alpha", 10);          // re-insert into tombstone
            dict_get(d, "alpha", v);
            printf("%d %d %llu\\n", gone, v, d->length);
            dict_destroy(d);
            dealloc(d);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1 10 2\n"
