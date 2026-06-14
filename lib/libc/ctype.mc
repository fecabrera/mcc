/**
 * Tests whether c is an alphanumeric character (A–Z, a–z, or 0–9).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is alphanumeric, zero otherwise
 */
@extern fn isalnum(c: int32) -> int32;

/**
 * Tests whether c is an alphabetic character (A–Z or a–z).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is alphabetic, zero otherwise
 */
@extern fn isalpha(c: int32) -> int32;

/**
 * Tests whether c is a blank character (space or horizontal tab).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is blank, zero otherwise
 */
@extern fn isblank(c: int32) -> int32;

/**
 * Tests whether c is a control character (0x00–0x1F or 0x7F).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is a control character, zero otherwise
 */
@extern fn iscntrl(c: int32) -> int32;

/**
 * Tests whether c is a decimal digit (0–9).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is a digit, zero otherwise
 */
@extern fn isdigit(c: int32) -> int32;

/**
 * Tests whether c is a printable non-space character (0x21–0x7E).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c has a visible glyph, zero otherwise
 */
@extern fn isgraph(c: int32) -> int32;

/**
 * Tests whether c is a lowercase letter (a–z).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is lowercase, zero otherwise
 */
@extern fn islower(c: int32) -> int32;

/**
 * Tests whether c is a printable character (0x20–0x7E, including space).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is printable, zero otherwise
 */
@extern fn isprint(c: int32) -> int32;

/**
 * Tests whether c is a punctuation character (printable, non-alphanumeric, non-space).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is punctuation, zero otherwise
 */
@extern fn ispunct(c: int32) -> int32;

/**
 * Tests whether c is a whitespace character (space, tab, newline, carriage
 * return, form feed, or vertical tab).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is whitespace, zero otherwise
 */
@extern fn isspace(c: int32) -> int32;

/**
 * Tests whether c is an uppercase letter (A–Z).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is uppercase, zero otherwise
 */
@extern fn isupper(c: int32) -> int32;

/**
 * Tests whether c is a hexadecimal digit (0–9, a–f, or A–F).
 *
 * @param c: character to test (as unsigned char value or EOF)
 *
 * @return non-zero if c is a hex digit, zero otherwise
 */
@extern fn isxdigit(c: int32) -> int32;

/**
 * Converts an uppercase letter to its lowercase equivalent.
 *
 * @param c: character to convert (as unsigned char value or EOF)
 *
 * @return lowercase equivalent of c, or c unchanged if not uppercase
 */
@extern fn tolower(c: int32) -> int32;

/**
 * Converts a lowercase letter to its uppercase equivalent.
 *
 * @param c: character to convert (as unsigned char value or EOF)
 *
 * @return uppercase equivalent of c, or c unchanged if not lowercase
 */
@extern fn toupper(c: int32) -> int32;
