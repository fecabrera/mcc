/***************************************
 * Constants
 ***************************************/

const EOF = -1;

/***************************************
 * Formatted output
 ***************************************/

/**
 * Writes output to stdout using a printf-style format.
 * Supports: %d/%i (signed int), %u (unsigned int), %x (lowercase hex), %X (uppercase hex),
 * %s (string), %c (char), %%. Width modifier N supported for all specifiers: %Nd space-pads on
 * the left (e.g. %8d), %0Nd zero-pads (e.g. %08x). For strings, %-Ns left-aligns within width N
 * (padding on the right); %Ns right-aligns (padding on the left).
 *
 * @param format: printf-style format string
 * @param ...:    variadic arguments matching the format specifiers
 *
 * @return number of characters written
 */
@extern fn printf(format: uint8*, ...) -> int32;

/**
 * Like printf, but takes a pre-initialized va_list instead of variadic arguments.
 *
 * @param format: printf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of characters written
 */
@extern fn vprintf(format: uint8*, args: va_list) -> int32;

/**
 * Formats a string into str using a printf-style format.
 * Supports: %d/%i (signed int), %u (unsigned int), %x (lowercase hex), %X (uppercase hex),
 * %s (string), %c (char), %%. Width modifier N supported for all specifiers: %Nd space-pads on
 * the left (e.g. %8d), %0Nd zero-pads (e.g. %08x). For strings, %-Ns left-aligns within width N
 * (padding on the right); %Ns right-aligns (padding on the left).
 *
 * @param str:    output buffer
 * @param format: printf-style format string
 * @param ...:    variadic arguments matching the format specifiers
 *
 * @return number of characters written, not including the null terminator
 */
@extern fn sprintf(str: uint8*, format: uint8*, ...) -> int32;

/**
 * Like sprintf, but writes at most size-1 characters plus a NUL terminator,
 * so it cannot overflow str. Prefer this over sprintf for fixed buffers.
 *
 * @param str:    output buffer
 * @param size:   capacity of str in bytes, including the NUL terminator
 * @param format: printf-style format string
 * @param ...:    variadic arguments matching the format specifiers
 *
 * @return number of characters that would have been written (excluding the
 *         NUL), which may exceed size-1 if the output was truncated
 */
@extern fn snprintf(str: uint8*, size: uint64, format: uint8*, ...) -> int32;

/**
 * Formats a string into str using a printf-style format and a pre-initialized va_list.
 * Supports: %d/%i (signed int), %u (unsigned int), %x (lowercase hex), %X (uppercase hex),
 * %s (string), %c (char), %%. Width modifier N supported for all specifiers: %Nd space-pads on
 * the left (e.g. %8d), %0Nd zero-pads (e.g. %08x). For strings, %-Ns left-aligns within width N
 * (padding on the right); %Ns right-aligns (padding on the left).
 *
 * @param str:    output buffer
 * @param format: printf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of characters written, not including the null terminator
 */
@extern fn vsprintf(str: uint8*, format: uint8*, args: va_list) -> int32;

/**
 * Like vsprintf, but writes at most size-1 characters plus a NUL terminator,
 * so it cannot overflow str. Prefer this over vsprintf for fixed buffers.
 *
 * @param str:    output buffer
 * @param size:   capacity of str in bytes, including the NUL terminator
 * @param format: printf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of characters that would have been written (excluding the
 *         NUL), which may exceed size-1 if the output was truncated
 */
@extern fn vsnprintf(str: uint8*, size: uint64, format: uint8*, args: va_list) -> int32;

/***************************************
 * Formatted input
 ***************************************/

/**
 * Reads formatted input from stdin, storing the converted values through the
 * pointer arguments. Conversion specifiers mirror the output ones.
 *
 * @param format: scanf-style format string
 * @param ...:    pointers to the objects that receive the converted values
 *
 * @return number of items successfully assigned, or EOF on input failure
 *         before any conversion
 */
@extern fn scanf(format: uint8*, ...) -> int32;

/**
 * Like scanf, but reads from the null-terminated string str instead of stdin.
 *
 * @param str:    input string to parse
 * @param format: scanf-style format string
 * @param ...:    pointers to the objects that receive the converted values
 *
 * @return number of items successfully assigned, or EOF if the end of the
 *         string is reached before any conversion
 */
@extern fn sscanf(str: uint8*, format: uint8*, ...) -> int32;

/**
 * Like scanf, but takes a pre-initialized va_list of pointers.
 *
 * @param format: scanf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of items successfully assigned, or EOF on input failure
 *         before any conversion
 */
@extern fn vscanf(format: uint8*, args: va_list) -> int32;

/**
 * Like sscanf, but takes a pre-initialized va_list of pointers.
 *
 * @param str:    input string to parse
 * @param format: scanf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of items successfully assigned, or EOF if the end of the
 *         string is reached before any conversion
 */
@extern fn vsscanf(str: uint8*, format: uint8*, args: va_list) -> int32;

/***************************************
 * Character input/output
 ***************************************/

/**
 * Reads the next character from stdin.
 *
 * @return the character read, as an unsigned char widened to int, or EOF on
 *         end of input or error
 */
@extern fn getchar() -> int32;

/**
 * Writes the character ch to stdout.
 *
 * @param ch: character to write (converted to unsigned char)
 *
 * @return the character written, or EOF on error
 */
@extern fn putchar(ch: int32) -> int32;

/**
 * Writes the null-terminated string str, followed by a newline, to stdout.
 *
 * @param str: null-terminated string to write
 *
 * @return a non-negative value on success, or EOF on error
 */
@extern fn puts(str: uint8*) -> int32;

/***************************************
 * File operations
 ***************************************/

/**
 * Deletes the file at the given path.
 *
 * @param filename: null-terminated path of the file to delete
 *
 * @return 0 on success, non-zero on failure
 */
@extern fn remove(filename: uint8*) -> int32;

/**
 * Renames a file, moving it across directories if necessary.
 *
 * @param old_name: existing file path
 * @param new_name: new file path
 *
 * @return 0 on success, non-zero on failure
 */
@extern fn rename(old_name: uint8*, new_name: uint8*) -> int32;

/***************************************
 * Error reporting
 ***************************************/

/**
 * Writes str, a colon, and a textual description of the current errno value
 * to stderr, followed by a newline.
 *
 * @param str: message prefix; if null or empty, only the error description is written
 */
@extern fn perror(str: uint8*);
