/***************************************
 * Constants
 ***************************************/

// Divide a clock() result by this to get seconds.
const CLOCKS_PER_SEC = 1000000;

/***************************************
 * Types
 ***************************************/

// time_t and clock_t are 64-bit integers on the LP64 platforms mcc targets;
// the bindings below spell them as int64 directly (mcc has no type aliases).

// Broken-down calendar time. The layout matches the C `struct tm` on macOS and
// glibc -- the nine standard int fields followed by the BSD/glibc tm_gmtoff and
// tm_zone extensions -- so a value can be passed to mktime/strftime safely.
struct tm {
    tm_sec: int32;     // seconds after the minute [0, 60]
    tm_min: int32;     // minutes after the hour [0, 59]
    tm_hour: int32;    // hours since midnight [0, 23]
    tm_mday: int32;    // day of the month [1, 31]
    tm_mon: int32;     // months since January [0, 11]
    tm_year: int32;    // years since 1900
    tm_wday: int32;    // days since Sunday [0, 6]
    tm_yday: int32;    // days since January 1 [0, 365]
    tm_isdst: int32;   // daylight saving: >0 in effect, 0 not, <0 unknown
    tm_gmtoff: int64;  // offset from UTC in seconds (BSD/glibc extension)
    tm_zone: char*;   // timezone abbreviation (BSD/glibc extension)
}

/***************************************
 * Time
 ***************************************/

/**
 * Returns the current calendar time as a time_t (seconds since the epoch), and
 * also stores it through timer if it is non-null.
 *
 * @param timer: optional int64* that also receives the result, or null
 *
 * @return the current time, or -1 if it is unavailable
 */
@extern fn time(timer: int64*) -> int64;

/**
 * Returns the difference end - start in seconds.
 *
 * @param end:   the later time
 * @param start: the earlier time
 *
 * @return end - start, in seconds
 */
@extern fn difftime(end: int64, start: int64) -> float64;

/**
 * Returns the processor time used by the program, in clock ticks. Divide by
 * CLOCKS_PER_SEC for seconds.
 *
 * @return elapsed processor time in clock ticks, or -1 if unavailable
 */
@extern fn clock() -> int64;

/**
 * Converts a broken-down local time to a time_t, normalizing out-of-range
 * fields and filling in tm_wday and tm_yday.
 *
 * @param tm: broken-down local time (modified in place to normalize it)
 *
 * @return the corresponding time_t, or -1 if it cannot be represented
 */
@extern fn mktime(tm: struct tm*) -> int64;

/***************************************
 * Conversion
 ***************************************/

/**
 * Converts a time_t to broken-down local time.
 *
 * @param timer: int64* holding the time to convert
 *
 * @return pointer to a static struct tm (overwritten by later calls), or null
 */
@extern fn localtime(timer: int64*) -> struct tm*;

/**
 * Converts a time_t to broken-down UTC time.
 *
 * @param timer: int64* holding the time to convert
 *
 * @return pointer to a static struct tm (overwritten by later calls), or null
 */
@extern fn gmtime(timer: int64*) -> struct tm*;

/**
 * Formats a broken-down time as a fixed 26-character string of the form
 * "Wed Jun 30 21:49:08 1993\n".
 *
 * @param tm: broken-down time to format
 *
 * @return pointer to a static string (overwritten by later calls)
 */
@extern fn asctime(tm: struct tm*) -> char*;

/**
 * Equivalent to asctime(localtime(timer)): a fixed 26-character local-time
 * string.
 *
 * @param timer: int64* holding the time to convert
 *
 * @return pointer to a static string (overwritten by later calls)
 */
@extern fn ctime(timer: int64*) -> char*;

/**
 * Formats tm into s according to format (strftime conversion specifiers such as
 * %Y, %m, %d, %H, %M, %S), writing at most max bytes including the NUL.
 *
 * @param s:      destination buffer
 * @param max:    capacity of s in bytes, including the NUL terminator
 * @param format: strftime format string
 * @param tm:     broken-down time to format
 *
 * @return number of bytes written (excluding the NUL), or 0 if it did not fit
 */
@extern fn strftime(s: char*, max: uint64, format: char*, tm: struct tm*) -> uint64;
