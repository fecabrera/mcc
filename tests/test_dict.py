"""lib/dict.mc: the owning, content-keyed string map, over mut/const receivers
(stage 3 of the libmc receiver migration)."""

from helpers import run, run_path


def test_direct_receiver_with_growth():
    # The post-migration idiom: a local dict passes directly, no `&`.
    # Capacity 2 forces the grow path (mut-to-mut re-lending inside .set).
    assert run(
        """
        import "std/dict";
        fn main() -> int32 {
            let d = dict<int32>(2);
            d.set("one", 1);
            d.set("two", 2);
            d.set("three", 3);
            d.set("two", 22);                  // update in place, same length
            let v: int32 = 0;
            if (!d.get("two", v)) return 100;
            if (v != 22) return 101;
            if (d.length != 3) return 102;
            if (d.capacity < 4) return 103;    // grew from 2
            d.remove("one");
            if (d.get("one", v)) return 104;
            return 0;
        }
        """
    ) == 0


def test_amp_call_sites_still_compile():
    # Pre-migration `&x` call shapes keep working via pointer decay.
    assert run(
        """
        import "std/dict";
        fn main() -> int32 {
            let d = dict<int32>(4);
            d.set("k", 7);
            let v: int32 = 0;
            let found = d.get("k", v);
            return (found and v == 7) ? 0 : 1;
        }
        """
    ) == 0


def test_iteration_visits_all_entries():
    # Drives dict `next`, instantiating it -- which writes the entry to the out
    # pair via a `dict_entry as pair` value upcast (see lib/dict.mc).
    assert run(
        """
        import "std/dict";
        fn main() -> int32 {
            let d = alloc<struct dict<uint64>>(1);
            if (d == null) return 1;    // proves d for the receiver slots below
            dict::constructor(d, 8);
            dict::set(d, "a", 10);
            dict::set(d, "b", 20);
            dict::set(d, "c", 30);
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
        import "std/dict";
        fn main() -> int32 {
            let d = alloc<struct dict<uint64>>(1);
            if (d == null) return 1;    // proves d for the receiver slots below
            dict::constructor(d, 8);
            dict::set(d, "a", 10);
            dict::set(d, "b", 20);
            dict::set(d, "c", 30);
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
        import "std/dict";
        import "libc/stdio";
        fn main() -> int32 {
            let d = alloc<struct dict<int32>>(1);
            if (d == null) { return 1; }  // proves d for the receiver slots
            dict::constructor(d, 4);
            dict::set(d, "hello", 42);
            let v: int32 = 0;
            if (dict::get(d, "hello", v))
                printf("found %d\\n", v);
            dict::set(d, "hello", 43);          // update via another pointer
            dict::get(d, "hello", v);
            printf("updated %d, length %llu\\n", v, d->length);
            if (!dict::get(d, "absent", v))
                puts("absent missing");
            dict::destructor(d);
            dealloc(d);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "found 42\nupdated 43, length 1\nabsent missing\n"


def test_dict_owns_key_copies(tmp_path, capfd):
    # One scratch buffer, rewritten for every insert: entries can only stay
    # distinct if .set copied the key.
    main = tmp_path / "main.mc"
    main.write_text(
        """
        import "std/dict";
        import "libc/stdio";
        fn main() -> int32 {
            let d = alloc<struct dict<int32>>(1);
            if (d == null) { return 1; }  // narrows d, loops keep it
            dict::constructor(d, 2);

            let scratch = alloc<char>(3);
            if (scratch == null) { return 1; }  // narrows scratch, loops keep it
            let i: int32 = 0;
            while (i < 100) {
                scratch[0] = (65 + i / 10) as char;
                scratch[1] = (65 + i % 10) as char;
                scratch[2] = 0;
                dict::set(d!, scratch, i * 7);   // heap key into @nonnull, in-loop;
                                                // d at a mut receiver in a loop
                                                // drops its narrowed fact, so !
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
                if (!dict::get(d!, scratch, v))
                    errors = errors + 1;
                else if (v != i * 7)
                    errors = errors + 1;
                i = i + 1;
            }
            printf("%d %llu %llu\\n", errors, d->length, d->capacity);
            dealloc(scratch);
            dict::destructor(d!);               // the loops killed d's fact for good
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
        import "std/dict";
        import "libc/stdio";
        fn main() -> int32 {
            let d = alloc<struct dict<int32>>(1);
            if (d == null) { return 1; }  // proves d for the receiver slots
            dict::constructor(d, 8);
            dict::set(d, "alpha", 1);
            dict::set(d, "beta", 2);
            dict::remove(d, "alpha");
            let v: int32 = 0;
            let gone = !dict::get(d, "alpha", v);
            dict::set(d, "alpha", 10);          // re-insert into tombstone
            dict::get(d, "alpha", v);
            printf("%d %d %llu\\n", gone, v, d->length);
            dict::destructor(d);
            dealloc(d);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1 10 2\n"
