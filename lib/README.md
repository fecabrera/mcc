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
| [array.mc](array.mc) | `import "array";` | Growable generic `array<T>`: `array_init`/`array_destroy`/`array_reset`, `array_get`/`array_set`, `array_append` (auto-grows at 70% load). |
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
| [libc/stdlib.mc](libc/stdlib.mc) | `import "libc/stdlib";` | `atoi`, `atol`, `atoll`. |
| [libc/string.mc](libc/string.mc) | `import "libc/string";` | `strcpy`/`strncpy`, `strlen`/`strnlen`, `strcmp`/`strncmp`, `memcmp`/`memset`/`memcpy`/`memmove`. |
| [libc/ctype.mc](libc/ctype.mc) | `import "libc/ctype";` | Character classification (`isalpha`, `isdigit`, `isspace`, …) and `tolower`/`toupper`. |
| [libc/limits.mc](libc/limits.mc) | `import "libc/limits";` | Integer limit constants: `INT_MAX`, `LONG_MIN`, `UCHAR_MAX`, … |
