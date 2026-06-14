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
// @extern fn vsprintf(str: uint8*, format: uint8*, __builtin_va_list args) -> int32;

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