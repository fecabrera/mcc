"""lib/dict.mc: the owning, content-keyed string map."""

from helpers import run_path


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
            if (dict_get(d, "hello", &v))
                printf("found %d\\n", v);
            dict_set(d, "hello", 43);          // update via another pointer
            dict_get(d, "hello", &v);
            printf("updated %d, length %llu\\n", v, d->length);
            if (!dict_get(d, "absent", &v))
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

            let scratch = alloc<uint8>(3);
            let i: int32 = 0;
            while (i < 100) {
                scratch[0] = (65 + i / 10) as uint8;
                scratch[1] = (65 + i % 10) as uint8;
                scratch[2] = 0;
                dict_set(d, scratch, i * 7);
                i = i + 1;
            }
            scratch[0] = 90;  // clobber the caller's buffer
            scratch[1] = 0;

            let errors: int32 = 0;
            let v: int32 = 0;
            i = 0;
            while (i < 100) {
                scratch[0] = (65 + i / 10) as uint8;
                scratch[1] = (65 + i % 10) as uint8;
                scratch[2] = 0;
                if (!dict_get(d, scratch, &v))
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
            let gone = !dict_get(d, "alpha", &v);
            dict_set(d, "alpha", 10);          // re-insert into tombstone
            dict_get(d, "alpha", &v);
            printf("%d %d %llu\\n", gone, v, d->length);
            dict_destroy(d);
            dealloc(d);
            return 0;
        }
        """
    )
    assert run_path(main) == 0
    assert capfd.readouterr().out == "1 10 2\n"
