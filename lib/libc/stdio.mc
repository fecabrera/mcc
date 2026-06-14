/***************************************
 * Constants
 ***************************************/

const EOF = -1;

// `whence` values for fseek.
const SEEK_SET = 0;   // from the start of the file
const SEEK_CUR = 1;   // from the current position
const SEEK_END = 2;   // from the end of the file

// Default stream buffer size, for setvbuf/setbuf. Platform-specific.
@if (TARGET_OS == OS_DARWIN) {
    const BUFSIZ = 1024;
} @else {
    const BUFSIZ = 8192;
}

// `mode` values for setvbuf.
const _IOFBF = 0;   // fully buffered
const _IOLBF = 1;   // line buffered
const _IONBF = 2;   // unbuffered

/***************************************
 * Streams (FILE*)
 ***************************************/

// An opaque handle to an open stream. Its layout belongs to the C library, so
// it is only ever used through a pointer (struct FILE*).
struct FILE {}

// The three standard streams. Their underlying linker symbols differ by
// platform -- macOS exposes __stdinp/__stdoutp/__stderrp, while glibc exposes
// stdin/stdout/stderr -- so @if + @symbol bind the right ones behind a single
// set of names.
@if (TARGET_OS == OS_DARWIN) {
    @extern @symbol("__stdinp")  let stdin:  struct FILE*;
    @extern @symbol("__stdoutp") let stdout: struct FILE*;
    @extern @symbol("__stderrp") let stderr: struct FILE*;
} @else {
    @extern @symbol("stdin")  let stdin:  struct FILE*;
    @extern @symbol("stdout") let stdout: struct FILE*;
    @extern @symbol("stderr") let stderr: struct FILE*;
}

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

/***************************************
 * Opening and closing streams
 ***************************************/

/**
 * Opens the file at the given path and returns a stream for it.
 *
 * @param filename: null-terminated path of the file to open
 * @param mode:     access mode, e.g. "r", "w", "a", "rb", "r+"
 *
 * @return a stream on success, or null on failure (see errno)
 */
@extern fn fopen(filename: uint8*, mode: uint8*) -> struct FILE*;

/**
 * Reopens stream with a new file and/or mode, reusing the stream object.
 * Typically used to redirect a standard stream (e.g. stdout to a file).
 *
 * @param filename: path to associate with the stream, or null to keep the file
 * @param mode:     new access mode
 * @param stream:   the stream to reopen
 *
 * @return stream on success, or null on failure
 */
@extern fn freopen(filename: uint8*, mode: uint8*, stream: struct FILE*) -> struct FILE*;

/**
 * Flushes and closes a stream, releasing its resources.
 *
 * @param stream: the stream to close
 *
 * @return 0 on success, or EOF on error
 */
@extern fn fclose(stream: struct FILE*) -> int32;

/**
 * Writes any buffered output for stream to its file. A null stream flushes all
 * open output streams.
 *
 * @param stream: the stream to flush, or null for all output streams
 *
 * @return 0 on success, or EOF on error
 */
@extern fn fflush(stream: struct FILE*) -> int32;

/***************************************
 * Block input/output
 ***************************************/

/**
 * Reads up to count items of size bytes each from stream into ptr.
 *
 * @param ptr:    destination buffer
 * @param size:   size of each item in bytes
 * @param count:  number of items to read
 * @param stream: the stream to read from
 *
 * @return the number of complete items read, which is fewer than count at end
 *         of file or on error
 */
@extern fn fread(ptr: uint8*, size: uint64, count: uint64, stream: struct FILE*) -> uint64;

/**
 * Writes count items of size bytes each from ptr to stream.
 *
 * @param ptr:    source buffer
 * @param size:   size of each item in bytes
 * @param count:  number of items to write
 * @param stream: the stream to write to
 *
 * @return the number of complete items written, fewer than count on error
 */
@extern fn fwrite(ptr: uint8*, size: uint64, count: uint64, stream: struct FILE*) -> uint64;

/***************************************
 * Stream positioning
 ***************************************/

/**
 * Moves the file position for stream to offset bytes relative to whence
 * (SEEK_SET, SEEK_CUR, or SEEK_END).
 *
 * @param stream: the stream to reposition
 * @param offset: byte offset relative to whence
 * @param whence: SEEK_SET, SEEK_CUR, or SEEK_END
 *
 * @return 0 on success, non-zero on failure
 */
@extern fn fseek(stream: struct FILE*, offset: int64, whence: int32) -> int32;

/**
 * Reports the current file position for stream.
 *
 * @param stream: the stream to query
 *
 * @return the position in bytes from the start of the file, or -1 on error
 */
@extern fn ftell(stream: struct FILE*) -> int64;

/**
 * Resets the file position for stream to the beginning and clears its error
 * and end-of-file indicators.
 *
 * @param stream: the stream to rewind
 */
@extern fn rewind(stream: struct FILE*);

/***************************************
 * Stream character input/output
 ***************************************/

/**
 * Reads the next character from stream.
 *
 * @param stream: the stream to read from
 *
 * @return the character as an unsigned char widened to int, or EOF at end of
 *         input or on error
 */
@extern fn fgetc(stream: struct FILE*) -> int32;

/**
 * Like fgetc; reads the next character from stream.
 *
 * @param stream: the stream to read from
 *
 * @return the character read, or EOF at end of input or on error
 */
@extern fn getc(stream: struct FILE*) -> int32;

/**
 * Writes the character ch to stream.
 *
 * @param ch:     character to write (converted to unsigned char)
 * @param stream: the stream to write to
 *
 * @return the character written, or EOF on error
 */
@extern fn fputc(ch: int32, stream: struct FILE*) -> int32;

/**
 * Like fputc; writes the character ch to stream.
 *
 * @param ch:     character to write (converted to unsigned char)
 * @param stream: the stream to write to
 *
 * @return the character written, or EOF on error
 */
@extern fn putc(ch: int32, stream: struct FILE*) -> int32;

/**
 * Pushes the character ch back onto stream, so the next read returns it. At
 * most one character of pushback is guaranteed.
 *
 * @param ch:     character to push back
 * @param stream: the stream to push onto
 *
 * @return the character pushed back, or EOF on error
 */
@extern fn ungetc(ch: int32, stream: struct FILE*) -> int32;

/**
 * Reads a line from stream into str: at most size-1 characters, stopping after
 * a newline (which is kept) or at end of file, then NUL-terminates.
 *
 * @param str:    destination buffer
 * @param size:   capacity of str in bytes, including the NUL terminator
 * @param stream: the stream to read from
 *
 * @return str on success, or null at end of file before any character or on error
 */
@extern fn fgets(str: uint8*, size: int32, stream: struct FILE*) -> uint8*;

/**
 * Writes the null-terminated string str to stream (no newline is added).
 *
 * @param str:    null-terminated string to write
 * @param stream: the stream to write to
 *
 * @return a non-negative value on success, or EOF on error
 */
@extern fn fputs(str: uint8*, stream: struct FILE*) -> int32;

/***************************************
 * Stream formatted input/output
 ***************************************/

/**
 * Writes output to stream using a printf-style format.
 *
 * @param stream: the stream to write to
 * @param format: printf-style format string
 * @param ...:    variadic arguments matching the format specifiers
 *
 * @return number of characters written, or a negative value on error
 */
@extern fn fprintf(stream: struct FILE*, format: uint8*, ...) -> int32;

/**
 * Like fprintf, but takes a pre-initialized va_list instead of variadic arguments.
 *
 * @param stream: the stream to write to
 * @param format: printf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of characters written, or a negative value on error
 */
@extern fn vfprintf(stream: struct FILE*, format: uint8*, args: va_list) -> int32;

/**
 * Reads formatted input from stream, storing converted values through the
 * pointer arguments.
 *
 * @param stream: the stream to read from
 * @param format: scanf-style format string
 * @param ...:    pointers to the objects that receive the converted values
 *
 * @return number of items assigned, or EOF on input failure before any conversion
 */
@extern fn fscanf(stream: struct FILE*, format: uint8*, ...) -> int32;

/**
 * Like fscanf, but takes a pre-initialized va_list of pointers.
 *
 * @param stream: the stream to read from
 * @param format: scanf-style format string
 * @param args:   variadic argument list (must be initialized by the caller)
 *
 * @return number of items assigned, or EOF on input failure before any conversion
 */
@extern fn vfscanf(stream: struct FILE*, format: uint8*, args: va_list) -> int32;

/***************************************
 * Stream buffering and state
 ***************************************/

/**
 * Sets the buffer stream uses for I/O. A null buffer makes stream unbuffered;
 * otherwise buf must hold at least BUFSIZ bytes. Call before any I/O on stream.
 *
 * @param stream: the stream to configure
 * @param buf:    a buffer of at least BUFSIZ bytes, or null for unbuffered
 */
@extern fn setbuf(stream: struct FILE*, buf: uint8*);

/**
 * Sets the buffering mode and buffer for stream. Call before any I/O on stream.
 *
 * @param stream: the stream to configure
 * @param buf:    buffer to use, or null to let the library allocate one
 * @param mode:   _IOFBF (full), _IOLBF (line), or _IONBF (none)
 * @param size:   size of buf in bytes
 *
 * @return 0 on success, non-zero on failure
 */
@extern fn setvbuf(stream: struct FILE*, buf: uint8*, mode: int32, size: uint64) -> int32;

/**
 * Clears the end-of-file and error indicators for stream.
 *
 * @param stream: the stream to reset
 */
@extern fn clearerr(stream: struct FILE*);

/**
 * Tests the end-of-file indicator for stream.
 *
 * @param stream: the stream to query
 *
 * @return non-zero if the end-of-file indicator is set, otherwise 0
 */
@extern fn feof(stream: struct FILE*) -> int32;

/**
 * Tests the error indicator for stream.
 *
 * @param stream: the stream to query
 *
 * @return non-zero if the error indicator is set, otherwise 0
 */
@extern fn ferror(stream: struct FILE*) -> int32;
