const CHAR_BIT = sizeof(char);

const SCHAR_MAX = 127;
const SCHAR_MIN = (-SCHAR_MAX - 1);
const UCHAR_MAX = 255;

/* AArch64: char is unsigned by default */
const CHAR_MIN = 0;
const CHAR_MAX = UCHAR_MAX;

const SHRT_MAX = 32767;
const SHRT_MIN = (-SHRT_MAX - 1);
const USHRT_MAX = 65535;

const INT_MAX = 2147483647;
const INT_MIN = (-INT_MAX - 1);
const UINT_MAX = 4294967295;

/* LP64: long is 64-bit on AArch64 */
const LONG_MAX = 9223372036854775807;
const LONG_MIN = (-LONG_MAX - 1);
const ULONG_MAX = 18446744073709551615;

const LLONG_MAX = LONG_MAX;
const LLONG_MIN = LONG_MIN;
const ULLONG_MAX = ULONG_MAX;