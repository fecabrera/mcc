import "libc/ctype";

/**
 * Reports whether the character is alphabetic.
 *
 * @param self: character to classify
 *
 * @return true if self is a letter
 */
@inline
fn char::is_alpha(const self: char) -> bool {
    return isalpha(self as int32) != 0;
}

/**
 * Reports whether the character is alphanumeric.
 *
 * @param self: character to classify
 *
 * @return true if self is a letter or a decimal digit
 */
@inline
fn char::is_alnum(const self: char) -> bool {
    return isalnum(self as int32) != 0;
}

/**
 * Reports whether the character is a decimal digit.
 *
 * @param self: character to classify
 *
 * @return true if self is one of '0'..'9'
 */
@inline
fn char::is_digit(const self: char) -> bool {
    return isdigit(self as int32) != 0;
}

/**
 * Reports whether the character is a hexadecimal digit.
 *
 * @param self: character to classify
 *
 * @return true if self is one of '0'..'9', 'a'..'f', or 'A'..'F'
 */
@inline
fn char::is_hex(const self: char) -> bool {
    return isxdigit(self as int32) != 0;
}

/**
 * Reports whether the character is whitespace (space, tab, newline,
 * vertical tab, form feed, or carriage return).
 *
 * @param self: character to classify
 *
 * @return true if self is a whitespace character
 */
@inline
fn char::is_space(const self: char) -> bool {
    return isspace(self as int32) != 0;
}

/**
 * Reports whether the character is an uppercase letter.
 *
 * @param self: character to classify
 *
 * @return true if self is an uppercase letter
 */
@inline
fn char::is_upper(const self: char) -> bool {
    return isupper(self as int32) != 0;
}

/**
 * Reports whether the character is a lowercase letter.
 *
 * @param self: character to classify
 *
 * @return true if self is a lowercase letter
 */
@inline
fn char::is_lower(const self: char) -> bool {
    return islower(self as int32) != 0;
}

/**
 * Converts the character to uppercase. A character with no uppercase
 * form is returned unchanged.
 *
 * @param self: character to convert
 *
 * @return the uppercase equivalent of self, or self itself
 */
@inline
fn char::upper(const self: char) -> char {
    return toupper(self as int32) as char;
}

/**
 * Converts the character to lowercase. A character with no lowercase
 * form is returned unchanged.
 *
 * @param self: character to convert
 *
 * @return the lowercase equivalent of self, or self itself
 */
@inline
fn char::lower(const self: char) -> char {
    return tolower(self as int32) as char;
}
