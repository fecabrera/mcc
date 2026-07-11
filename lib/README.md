# Standard library

The `lib/` root is on the import search path by default, so its modules import
under their prefix — `std/` for the mcc modules (`import "std/memory";`,
`import "std/io";`, `import "std/hashing/md5";`) and `libc/` for the C bindings.
Pass `--nostdlib` to leave `lib/` off the path. Every function carries a doc
comment; this page is the index.

The `libc/` modules are `@extern` declarations for functions provided by the C
library you link against (or your own freestanding implementation); see
[Reaching libc](../docs/language.md#reaching-libc). Everything else, under
`std/`, is implemented in mcc.

## Core

| Module                 | Import             | Provides                                                                                                                                                                                                                                                                                                     |
| ---------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [std/equality.mc](std/equality.mc) | `import "std/equality";` | The equality protocol's baseline overload set: `equals<T>(const self: slice<T>, const str: slice<T>) -> bool` compares two slices element by element (different lengths are never equal; empty slices compare equal; `T` must support `!=`). A string borrows in and a literal adapts, so `equals(s, "hi")` works directly. Open overload sets make it extensible: a type joins by adding an `equals` overload in its own module. |
| [std/format.mc](std/format.mc) | `import "std/format";` | The formatting protocol's baseline overload set: every `format(mut str: string, value: X, const modifier: slice<char>)` member appends `value`'s rendering to `str` (a bare string literal adapts to the modifier, so `format(s, 255 as int32, "x")` works), with closed signed/unsigned integer groups (modifier grammar `[0][width][x|X|b|p]`: `x`/`X` hex, `b` binary, `p` pointer, width and zero-padding; narrow widths funnel into the hand-rolled `uint64` digit worker, negatives rendering sign-and-magnitude), `float64`, `bool` (`y`/`yes`), `char`/`char*`/`slice<char>` as text (string members take `[N][s][N]` field widths — `20s` right-aligns, `s20` left-aligns — and a null `char*` renders `(null)`), `slice<char*>` as a quoted list, `slice<T>` as a bracketed per-element list, and an unbounded `<typename>` fallback. Open overload sets make it extensible: one `format` overload in your own module makes your type printable (see [Formatting](../docs/language.md#formatting)). |
| [std/memory.mc](std/memory.mc) | `import "std/memory";` | Typed heap allocation over `malloc`/`realloc`/`free`: `alloc<T>`, `new<T>`, `resize<T>`, `dealloc<T>`, `bytecopy<T>`, `copy<T>`, `bytefill<T>`, `fill<T>`, `bytezero<T>`, `zero<T>` (`copy_bytes`/`copy_items`/`set_bytes`/`set_items` remain as `@deprecated` aliases that warn at each call site; and the raw `malloc`/`realloc`/`free`/`memcpy`/`memset`). |
| [std/io.mc](std/io.mc)       | `import "std/io";`    | Variadic `print` and `println` — `{[modifiers]}` placeholders rendered type-driven through the `std/format` overload set ([Formatted print/println](../docs/language.md#formatted-print--println)) — plus `writestr`/`writeln`/`writechar` (the writers take any `T extends slice<char>`, so a `string` or `list<char>` writes with no explicit borrow), and the [`panic`/`assert`](../docs/language.md#panic-and-assert) guards — a verbatim-message and an `@format`-collecting overload of each, writing `panic: ...` / `assertion failed: ...` to standard error and aborting (`panic` is `@noreturn`; stdout is flushed first).                                                                                                                                                                                                                        |
| [std/utils.mc](std/utils.mc)     | `import "std/utils";`   | The generic in-place helpers `swap` (exchange two values) and `replace` (store a value, return the old one), built on `mut` parameters.                                                                                                                                                                                                                                                                                       |

## Data structures

| Module                                 | Import                     | Provides                                                                                                                                                                                                                                                                                  |
| -------------------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [std/list.mc](std/list.mc)                     | `import "std/list";`           | Growable generic `list<T>`: `list_init`/`list_destroy`/`list_reset`, `list_get`/`list_set` (checked), `list_has`/`list_at` (the domain predicate and the unchecked `mut`-lvalue accessor: `list_at(xs, i) = v` writes in place), `list_push` (one element; doubles when full), and `list_append`, plus `list_it`/`list_next` iteration. `list_init` and `list_append` are overloaded on their source: a `const slice<T>` copies any borrowed run (a list borrows in with `as`), a `(T*, n)` pair copies a raw array.                                                                  |
| [std/stack.mc](std/stack.mc)                   | `import "std/stack";`          | Growable generic LIFO `stack<T>`: `stack_init`/`stack_destroy`, `stack_push`/`stack_pop`/`stack_peek`, `stack_len`/`stack_is_empty` (doubles when full).                                                                                                                                  |
| [std/queue.mc](std/queue.mc)                   | `import "std/queue";`          | Linked-list generic FIFO `queue<T>`: `queue_init`/`queue_destroy`, `queue_push`/`queue_pop`/`queue_peek`, `queue_is_empty`, and `queue_it`/`queue_next` iteration (front to back); O(1) push and pop, one heap node per value.                                                            |
| [std/ring.mc](std/ring.mc)                     | `import "std/ring";`           | Growable generic FIFO ring buffer `ring<T>`: `ring_init`/`ring_destroy`, `ring_push`/`ring_pop`/`ring_peek`, `ring_has`/`ring_at` (logical indexing from the front, wrap and all; `ring_at` is the unchecked `mut`-lvalue accessor — guard with `ring_has`), `ring_len`/`ring_is_empty` (doubles when full, re-laying wrapped elements in logical order).                                                                       |
| [std/set.mc](std/set.mc)                       | `import "std/set";`            | Open-addressing hash map `set<K, V>` keyed by value/identity: `set_init`/`set_destroy`, `set_set`/`set_get`/`set_remove`, and `set_it`/`set_next` iteration (yielding `pair<K, V>`).                                                                                                      |
| [std/dict.mc](std/dict.mc)                     | `import "std/dict";`           | String-keyed `dict<V>` that owns content-hashed copies of its keys: `dict_init`/`dict_destroy`, `dict_set`/`dict_get`/`dict_remove`, and `dict_it`/`dict_next` iteration (yielding `pair<char*, V>`).                                                                                    |
| [std/string.mc](std/string.mc)                 | `import "std/string";`         | Growable text string `string` (a `type string = list<char>`): `string_init`/`string_destroy`/`string_reset`, `string_get`/`string_set` (checked), `string_has`/`string_at` (unchecked `mut`-lvalue access: `string_at(s, 0) = '/'`), `string_push` (one char), `string_append`, `equals` (the string members of the [equality protocol](equality.mc): string-vs-slice and string-vs-string, a literal adapts so `equals(s, "hi")` works), and `string_it`/`string_next` iteration. `string_init` and `string_append` are overloaded on their source: a `const slice<char>` copies any borrowed run (a string borrows in with `as`, a literal adapts directly), a NUL-terminated `char*` copies to the terminator, a `(char*, n)` pair copies exactly n bytes.  |

The `iterator<T>` cursor these containers return from `_it`, and the
`pair<K, V>` the keyed ones yield from `_next`, are **compiler builtins** — no
import needed (see [Control flow](../docs/language.md#control-flow)).

## Hashing

| Module                                         | Import                         | Provides                                                                                                                   |
| ---------------------------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| [std/hash.mc](std/hash.mc)                             | `import "std/hash";`               | `hash<T>` overload set: integer keys by value (splitmix64), pointer keys by content (FNV-1a). The hook the containers use. |
| [std/hashing/splitmix64.mc](std/hashing/splitmix64.mc) | `import "std/hashing/splitmix64";` | `splitmix64<T>` — fast integer mix.                                                                                        |
| [std/hashing/fnv1a.mc](std/hashing/fnv1a.mc)           | `import "std/hashing/fnv1a";`      | `fnv1a<T>` — content hash of a NUL-terminated buffer, or of exactly `length` elements via the `slice<T>` member.           |
| [std/hashing/murmur3.mc](std/hashing/murmur3.mc)       | `import "std/hashing/murmur3";`    | `murmur3` — 32-bit MurmurHash3.                                                                                            |
| [std/hashing/crc32.mc](std/hashing/crc32.mc)           | `import "std/hashing/crc32";`      | `crc32` — CRC-32 checksum.                                                                                                 |
| [std/hashing/md5.mc](std/hashing/md5.mc)               | `import "std/hashing/md5";`        | `md5` — MD5 digest into a 16-byte buffer.                                                                                  |

## C bindings (`libc/`)

Pointer parameters that the C contract forbids to be null (the `str*`/`mem*`
arguments, `strto*`/`ato*` inputs, `time`/`strftime` fields, the pointer-out
math functions) are marked [`@nonnull`](../docs/language.md#-wextern-nonnull),
so a build under `-Wextern-nonnull` enforces that contract at every call site;
the default build leaves it unenforced.

| Module                           | Import                  | Provides                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [libc/stdio.mc](libc/stdio.mc)   | `import "libc/stdio";`  | Formatted I/O (`printf`/`sprintf`/`snprintf`/`scanf` families), `getchar`/`putchar`/`puts`, and `FILE*` streams: the `stdin`/`stdout`/`stderr` handles, `fopen`/`fclose`/`fflush`, `fread`/`fwrite`, `fseek`/`ftell`, `fgets`/`fputs`/`fgetc`/`fputc`, `fprintf`/`fscanf`, `feof`/`ferror`, plus `remove`/`rename`, `perror`, and `EOF`/`SEEK_*`. |
| [libc/stdlib.mc](libc/stdlib.mc) | `import "libc/stdlib";` | Memory (`malloc`/`calloc`/`realloc`/`aligned_alloc`/`free`), termination (`exit`/`_Exit`/`abort`/`atexit`), conversion (`atoi`/`atof`/`strtol` family/`strtod`), `rand`/`srand`, `qsort`/`bsearch`, `getenv`/`system`, `abs`/`labs`/`llabs`, `div`/`ldiv`/`lldiv` (with `div_t`/`ldiv_t`/`lldiv_t`), and `EXIT_SUCCESS`/`EXIT_FAILURE`/`RAND_MAX`.                                                        |
| [libc/string.mc](libc/string.mc) | `import "libc/string";` | Copy/concat (`strcpy`/`strncpy`/`strcat`/`strncat`), examine (`strlen`/`strnlen`/`strcmp`/`strncmp`/`strcoll`/`strxfrm`), search (`strchr`/`strrchr`/`strstr`/`strspn`/`strcspn`/`strpbrk`/`strtok`), memory (`memcmp`/`memset`/`memcpy`/`memmove`/`memchr`), and `strerror`.                                                                     |
| [libc/ctype.mc](libc/ctype.mc)   | `import "libc/ctype";`  | Character classification (`isalpha`, `isdigit`, `isspace`, …) and `tolower`/`toupper`.                                                                                                                                                                                                                                                            |
| [libc/math.mc](libc/math.mc)     | `import "libc/math";`   | Double-precision math: `sqrt`/`pow`/`hypot`, trig and hyperbolic, `exp`/`log` family, rounding (`floor`/`ceil`/`round`/`trunc`), `fmod`, `fabs`, gamma/erf, `fma`.                                                                                                                                                                                |
| [libc/limits.mc](libc/limits.mc) | `import "libc/limits";` | Integer limit constants: `INT_MAX`, `LONG_MIN`, `UCHAR_MAX`, …                                                                                                                                                                                                                                                                                    |
| [libc/float.mc](libc/float.mc)   | `import "libc/float";`  | `double` characteristics: `DBL_MAX`/`DBL_MIN`/`DBL_EPSILON`, `DBL_DIG`, exponent ranges, `FLT_RADIX`, `DECIMAL_DIG`.                                                                                                                                                                                                                              |
| [libc/time.mc](libc/time.mc)     | `import "libc/time";`   | `struct tm`, `time`/`clock`/`difftime`, `mktime`/`localtime`/`gmtime`, `asctime`/`ctime`/`strftime`, `CLOCKS_PER_SEC`.                                                                                                                                                                                                                            |
| [libc/errno.mc](libc/errno.mc)   | `import "libc/errno";`  | `errno`/`set_errno` (over the platform-specific location function) and the `EDOM`/`ERANGE`/`EILSEQ` codes.                                                                                                                                                                                                                                        |

### Coverage & roadmap

Where each C standard header stands. `size_t` is bound as `uint64`; single-precision
(`float`) variants are omitted (mcc's only float type is `float64`).

**stdio.h** — near-complete.

- [x] `printf` `vprintf` `sprintf` `snprintf` `vsprintf` `vsnprintf`
- [x] `scanf` `sscanf` `vscanf` `vsscanf`
- [x] `getchar` `putchar` `puts` · `remove` `rename` · `perror` · `EOF`
- [x] `stdin` `stdout` `stderr` (platform symbols, via `@if`/`@symbol`)
- [x] streams: `fopen` `freopen` `fclose` `fflush` · `fread` `fwrite` · `fseek` `ftell` `rewind`
- [x] stream chars/lines: `fgetc` `getc` `fputc` `putc` `ungetc` `fgets` `fputs`
- [x] stream formatted: `fprintf` `vfprintf` `fscanf` `vfscanf`
- [x] stream state: `setbuf` `setvbuf` `clearerr` `feof` `ferror`
- [x] consts: `SEEK_SET` `SEEK_CUR` `SEEK_END` `BUFSIZ` `_IOFBF` `_IOLBF` `_IONBF`
- [ ] `fgetpos` `fsetpos` (need an opaque `fpos_t`) · `tmpfile` `tmpnam`
- [ ] consts: `FOPEN_MAX` `FILENAME_MAX` `TMP_MAX` `L_tmpnam`

**stdlib.h** — near-complete.

- [x] `malloc` `calloc` `realloc` `aligned_alloc` `free`
- [x] `exit` `_Exit` `abort` `atexit` · consts `EXIT_SUCCESS` `EXIT_FAILURE`
- [x] `atoi` `atol` `atoll` `atof` · `strtol` `strtoll` `strtoul` `strtoull` `strtod`
- [x] `rand` `srand` (`RAND_MAX`) · `qsort` `bsearch`
- [x] `getenv` `system` · `abs` `labs` `llabs`
- [ ] `strtof` (no single-precision `float`) · `div` `ldiv` `lldiv` (need struct-return ABI)
- [ ] `quick_exit` `at_quick_exit` (absent on macOS libc)
- _out of scope:_ multibyte (`mblen`/`mbtowc`/`wctomb`/…)

**string.h** — ✅ complete.

- [x] `strcpy` `strncpy` `strcat` `strncat`
- [x] `strlen` `strnlen` `strcmp` `strncmp` `strcoll` `strxfrm`
- [x] `strchr` `strrchr` `strstr` `strspn` `strcspn` `strpbrk` `strtok`
- [x] `memcmp` `memset` `memcpy` `memmove` `memchr` · `strerror`

**ctype.h** — ✅ complete (`is*` classification + `tolower`/`toupper`).

**math.h** — ✅ complete (double-precision functions).

- [ ] classification macros: `isnan` `isinf` `isfinite` `signbit` `fpclassify`, and `HUGE_VAL` `INFINITY` `NAN` (all macros — need codegen or wrappers)

**limits.h** — ✅ complete (missing only the niche `MB_LEN_MAX`).

**time.h** — ✅ complete.

- [x] `time` `clock` `difftime` `mktime` · `localtime` `gmtime` `asctime` `ctime` `strftime`
- [x] `struct tm` (ABI-matching layout) · `CLOCKS_PER_SEC` · `time_t`/`clock_t` are `int64`

**errno.h** — ✅ complete.

- [x] `errno`/`set_errno` over the platform location fn (`__error` / `__errno_location`, via `@if`)
- [x] `EDOM` `ERANGE` `EILSEQ` (`strerror` lives in string.h)

**float.h** — ✅ complete for `double`.

- [x] `DBL_MAX` `DBL_MIN` `DBL_TRUE_MIN` `DBL_EPSILON` · `DBL_DIG` `DBL_MANT_DIG` · exponent ranges · `FLT_RADIX` `DECIMAL_DIG`
- _out of scope:_ `FLT_*`/`LDBL_*` (no `float32` or long double)

**Deliberately out of scope** (macro-shaped, platform-opaque, or niche):
`assert.h` (macro with file/line), `setjmp.h` (`setjmp` is a macro; `jmp_buf` is
platform-sized), `signal.h`, `locale.h`, `wchar.h`/`wctype.h`/`uchar.h`,
`complex.h`, `fenv.h`, `threads.h`/`stdatomic.h`, `tgmath.h`, `inttypes.h`.
`stdarg.h` is handled in the language ([`va_list`](../docs/language.md#variadic-functions));
`stddef.h`/`stdbool.h` are mostly built in (`null`, `bool`, the sized integer types).
