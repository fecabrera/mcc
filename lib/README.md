# Standard library

The modules under `lib/` are on the import search path by default, so they are
importable by bare name — `import "memory";`, `import "std";`,
`import "hashing/md5";`. Pass `--naked` to leave `lib/` off the path. Every
function carries a doc comment; this page is the index.

The `libc/` modules are `@extern` declarations for functions provided by the C
library you link against (or your own freestanding implementation) — an
alternative to the built-in [`#include <...>`](../README.md#includes) shim.
Everything else is implemented in mcc.

## Core

| Module | Import | Provides |
|--------|--------|----------|
| [memory.mc](memory.mc) | `import "memory";` | Typed heap allocation over `malloc`/`free`: `alloc<T>`, `dealloc<T>`, `copy_bytes<T>`, `set_bytes<T>`, `copy_items<T>`, `set_items<T>` (and the raw `malloc`/`free`/`memcpy`/`memset`). |
| [std.mc](std.mc) | `import "std";` | Variadic `print` and `println` — printf-style output, forwarding through a `va_list`. |

## Data structures

| Module | Import | Provides |
|--------|--------|----------|
| [array.mc](array.mc) | `import "array";` | Growable generic `array<T>`: `array_init`/`array_destroy`/`array_reset`, `array_get`/`array_set`, `array_append` (doubles when full), and `iter`/`next` iteration. |
| [set.mc](set.mc) | `import "set";` | Open-addressing hash map `set<K, V>` keyed by value/identity: `set_init`/`set_destroy`, `set_set`/`set_get`/`set_remove`. |
| [dict.mc](dict.mc) | `import "dict";` | String-keyed `dict<V>` that owns content-hashed copies of its keys: `dict_init`/`dict_destroy`, `dict_set`/`dict_get`/`dict_remove`. |

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
| [libc/stdio.mc](libc/stdio.mc) | `import "libc/stdio";` | Formatted I/O without `FILE*`: `printf`/`vprintf`, `sprintf`/`snprintf`/`vsprintf`/`vsnprintf`, the `scanf` family, `getchar`/`putchar`/`puts`, `remove`/`rename`, `perror`, and `EOF`. |
| [libc/stdlib.mc](libc/stdlib.mc) | `import "libc/stdlib";` | `malloc`/`calloc`/`realloc`/`free`, `exit`/`abort`, `abs`, and string conversion (`atoi`/`atol`/`atoll`). |
| [libc/string.mc](libc/string.mc) | `import "libc/string";` | `strcpy`/`strncpy`, `strlen`/`strnlen`, `strcmp`/`strncmp`, `memcmp`/`memset`/`memcpy`/`memmove`. |
| [libc/ctype.mc](libc/ctype.mc) | `import "libc/ctype";` | Character classification (`isalpha`, `isdigit`, `isspace`, …) and `tolower`/`toupper`. |
| [libc/math.mc](libc/math.mc) | `import "libc/math";` | Double-precision math: `sqrt`/`pow`/`hypot`, trig and hyperbolic, `exp`/`log` family, rounding (`floor`/`ceil`/`round`/`trunc`), `fmod`, `fabs`, gamma/erf, `fma`. |
| [libc/limits.mc](libc/limits.mc) | `import "libc/limits";` | Integer limit constants: `INT_MAX`, `LONG_MIN`, `UCHAR_MAX`, … |

### Coverage & roadmap

Where each C standard header stands. `size_t` is bound as `uint64`; single-precision
(`float`) variants are omitted (mcc's only float type is `float64`).

**stdio.h** — partial: no `FILE*` streams yet.
- [x] `printf` `vprintf` `sprintf` `snprintf` `vsprintf` `vsnprintf`
- [x] `scanf` `sscanf` `vscanf` `vsscanf`
- [x] `getchar` `putchar` `puts` · `remove` `rename` · `perror` · `EOF`
- [ ] streams: `fopen` `freopen` `fclose` `fflush` · `fread` `fwrite` · `fseek` `ftell` `rewind` `fgetpos` `fsetpos`
- [ ] stream chars/lines: `fgetc` `getc` `fputc` `putc` `ungetc` `fgets` `fputs`
- [ ] stream formatted: `fprintf` `vfprintf` `fscanf` `vfscanf`
- [ ] stream state: `setbuf` `setvbuf` `clearerr` `feof` `ferror` · `tmpfile` `tmpnam`
- [ ] `stdin` `stdout` `stderr` (platform symbols — need `@if`/`@symbol`)
- [ ] consts: `SEEK_SET` `SEEK_CUR` `SEEK_END` `BUFSIZ` `FOPEN_MAX` `FILENAME_MAX` `TMP_MAX` `L_tmpnam`

**stdlib.h** — partial.
- [x] `malloc` `calloc` `realloc` `free` · `exit` `abort` · `abs` · `atoi` `atol` `atoll`
- [ ] `atof` · `strtol` `strtoll` `strtoul` `strtoull` `strtod` `strtof`
- [ ] `rand` `srand` (`RAND_MAX`) · `qsort` `bsearch`
- [ ] `getenv` `system` · `labs` `llabs` · `div` `ldiv` `lldiv` (+ result structs)
- [ ] `_Exit` `atexit` `at_quick_exit` `quick_exit` · `aligned_alloc`
- [ ] consts: `EXIT_SUCCESS` `EXIT_FAILURE`
- _out of scope:_ multibyte (`mblen`/`mbtowc`/`wctomb`/…)

**string.h** — partial.
- [x] `strcpy` `strncpy` · `strlen` `strnlen` · `strcmp` `strncmp` · `memcmp` `memset` `memcpy` `memmove`
- [ ] `strcat` `strncat` · `strchr` `strrchr` `strstr` `memchr`
- [ ] `strspn` `strcspn` `strpbrk` `strtok` · `strerror` `strcoll` `strxfrm`

**ctype.h** — ✅ complete (`is*` classification + `tolower`/`toupper`).

**math.h** — ✅ complete (double-precision functions).
- [ ] classification macros: `isnan` `isinf` `isfinite` `signbit` `fpclassify`, and `HUGE_VAL` `INFINITY` `NAN` (all macros — need codegen or wrappers)

**limits.h** — ✅ complete (missing only the niche `MB_LEN_MAX`).

**Not yet started (whole headers):**
- [ ] **time.h** — `time` `clock` `difftime` `mktime` `localtime` `gmtime` `strftime`; `struct tm`, `time_t`, `clock_t`, `CLOCKS_PER_SEC`
- [ ] **errno.h** — `errno` (a macro: `*__error()` / `*__errno_location()`, needs `@if` + wrapper), `strerror`, `EDOM` `ERANGE` `EILSEQ`
- [ ] **float.h** — constants only: `DBL_MAX` `DBL_MIN` `DBL_EPSILON`, …

**Deliberately out of scope** (macro-shaped, platform-opaque, or niche):
`assert.h` (macro with file/line), `setjmp.h` (`setjmp` is a macro; `jmp_buf` is
platform-sized), `signal.h`, `locale.h`, `wchar.h`/`wctype.h`/`uchar.h`,
`complex.h`, `fenv.h`, `threads.h`/`stdatomic.h`, `tgmath.h`, `inttypes.h`.
`stdarg.h` is handled in the language ([`va_list`](../README.md#variadic-functions));
`stddef.h`/`stdbool.h` are mostly built in (`null`, `bool`, the sized integer types).
