# Standard library

The modules under `libmc/` are on the import search path by default, so they are
importable by bare name — `import "memory";`, `import "std";`,
`import "hashing/md5";`. Pass `--nostdlib` to leave `libmc/` off the path. Every
function carries a doc comment; this page is the index.

The `libc/` modules are `@extern` declarations for functions provided by the C
library you link against (or your own freestanding implementation); see
[Reaching libc](../docs/language.md#reaching-libc). Everything else is implemented
in mcc.

## Core

| Module | Import | Provides |
|--------|--------|----------|
| [memory.mc](memory.mc) | `import "memory";` | Typed heap allocation over `malloc`/`realloc`/`free`: `alloc<T>`, `new<T>`, `resize<T>`, `dealloc<T>`, `bytecopy<T>`, `copy<T>`, `set_bytes<T>`, `set_items<T>`, `bytezero<T>`, `zero<T>` (`copy_bytes`/`copy_items` remain as deprecated aliases; and the raw `malloc`/`realloc`/`free`/`memcpy`/`memset`). |
| [std.mc](std.mc) | `import "std";` | Variadic `print` and `println` — printf-style output, forwarding through a `va_list`. |

## Data structures

| Module | Import | Provides |
|--------|--------|----------|
| [list.mc](list.mc) | `import "list";` | Growable generic `list<T>`: `list_init`/`list_destroy`/`list_reset`, `list_get`/`list_set`, `list_append` (doubles when full), and `list_it`/`list_next` iteration. |
| [stack.mc](stack.mc) | `import "stack";` | Growable generic LIFO `stack<T>`: `stack_init`/`stack_destroy`, `stack_push`/`stack_pop`/`stack_peek`, `stack_len`/`stack_is_empty` (doubles when full). |
| [queue.mc](queue.mc) | `import "queue";` | Growable generic FIFO `queue<T>`, a ring buffer: `queue_init`/`queue_destroy`, `queue_push`/`queue_pop`/`queue_peek`/`queue_at`, `queue_len`/`queue_is_empty` (doubles when full). |
| [set.mc](set.mc) | `import "set";` | Open-addressing hash map `set<K, V>` keyed by value/identity: `set_init`/`set_destroy`, `set_set`/`set_get`/`set_remove`, and `set_it`/`set_next` iteration (yielding `pair<K, V>`). |
| [dict.mc](dict.mc) | `import "dict";` | String-keyed `dict<V>` that owns content-hashed copies of its keys: `dict_init`/`dict_destroy`, `dict_set`/`dict_get`/`dict_remove`, and `dict_it`/`dict_next` iteration (yielding `pair<uint8*, V>`). |
| [string.mc](string.mc) | `import "string";` | Growable byte string `string` (a `string extends list<uint8>`): `string_init`/`string_duplicate`/`string_destroy`/`string_reset`, `string_get`/`string_set`, `string_append`, `string_eq`, and `string_it`/`string_next` iteration. |
| [iteration/pair.mc](iteration/pair.mc) | `import "iteration/pair";` | `pair<K, V>` — the key/value element type the keyed containers yield from `<struct>_next`. |
| [range.mc](range.mc) | `import "range";` | Half-open integer `range<T>` ([start, end)) for counting loops: set `start`/`end`, then `for i in &r` via `range_it`/`range_next`. |

## Hashing

| Module | Import | Provides |
|--------|--------|----------|
| [hash.mc](hash.mc) | `import "hash";` | `hash<T>` overload set: integer keys by value (splitmix64), pointer keys by content (FNV-1a). The hook the containers use. |
| [hashing/splitmix64.mc](hashing/splitmix64.mc) | `import "hashing/splitmix64";` | `splitmix64<T>` — fast integer mix. |
| [hashing/fnv1a.mc](hashing/fnv1a.mc) | `import "hashing/fnv1a";` | `fnv1a<T>` — content hash of a NUL-terminated buffer. |
| [hashing/murmur3.mc](hashing/murmur3.mc) | `import "hashing/murmur3";` | `murmur3` — 32-bit MurmurHash3. |
| [hashing/crc32.mc](hashing/crc32.mc) | `import "hashing/crc32";` | `crc32` — CRC-32 checksum. |
| [hashing/md5.mc](hashing/md5.mc) | `import "hashing/md5";` | `md5` — MD5 digest into a 16-byte buffer. |

## C bindings (`libc/`)

| Module | Import | Provides |
|--------|--------|----------|
| [libc/stdio.mc](libc/stdio.mc) | `import "libc/stdio";` | Formatted I/O (`printf`/`sprintf`/`snprintf`/`scanf` families), `getchar`/`putchar`/`puts`, and `FILE*` streams: the `stdin`/`stdout`/`stderr` handles, `fopen`/`fclose`/`fflush`, `fread`/`fwrite`, `fseek`/`ftell`, `fgets`/`fputs`/`fgetc`/`fputc`, `fprintf`/`fscanf`, `feof`/`ferror`, plus `remove`/`rename`, `perror`, and `EOF`/`SEEK_*`. |
| [libc/stdlib.mc](libc/stdlib.mc) | `import "libc/stdlib";` | Memory (`malloc`/`calloc`/`realloc`/`aligned_alloc`/`free`), termination (`exit`/`_Exit`/`abort`/`atexit`), conversion (`atoi`/`atof`/`strtol` family/`strtod`), `rand`/`srand`, `qsort`/`bsearch`, `getenv`/`system`, `abs`/`labs`/`llabs`, and `EXIT_SUCCESS`/`EXIT_FAILURE`/`RAND_MAX`. |
| [libc/string.mc](libc/string.mc) | `import "libc/string";` | Copy/concat (`strcpy`/`strncpy`/`strcat`/`strncat`), examine (`strlen`/`strnlen`/`strcmp`/`strncmp`/`strcoll`/`strxfrm`), search (`strchr`/`strrchr`/`strstr`/`strspn`/`strcspn`/`strpbrk`/`strtok`), memory (`memcmp`/`memset`/`memcpy`/`memmove`/`memchr`), and `strerror`. |
| [libc/ctype.mc](libc/ctype.mc) | `import "libc/ctype";` | Character classification (`isalpha`, `isdigit`, `isspace`, …) and `tolower`/`toupper`. |
| [libc/math.mc](libc/math.mc) | `import "libc/math";` | Double-precision math: `sqrt`/`pow`/`hypot`, trig and hyperbolic, `exp`/`log` family, rounding (`floor`/`ceil`/`round`/`trunc`), `fmod`, `fabs`, gamma/erf, `fma`. |
| [libc/limits.mc](libc/limits.mc) | `import "libc/limits";` | Integer limit constants: `INT_MAX`, `LONG_MIN`, `UCHAR_MAX`, … |
| [libc/float.mc](libc/float.mc) | `import "libc/float";` | `double` characteristics: `DBL_MAX`/`DBL_MIN`/`DBL_EPSILON`, `DBL_DIG`, exponent ranges, `FLT_RADIX`, `DECIMAL_DIG`. |
| [libc/time.mc](libc/time.mc) | `import "libc/time";` | `struct tm`, `time`/`clock`/`difftime`, `mktime`/`localtime`/`gmtime`, `asctime`/`ctime`/`strftime`, `CLOCKS_PER_SEC`. |
| [libc/errno.mc](libc/errno.mc) | `import "libc/errno";` | `errno`/`set_errno` (over the platform-specific location function) and the `EDOM`/`ERANGE`/`EILSEQ` codes. |

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
