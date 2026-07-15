import "std/string";
import "std/format";

/**
 * Formats a slice of characters into a string using the format string. The
 * format string is a slice of characters that contains format specifiers for
 * the arguments. The format specifiers are replaced with the arguments when
 * the string is formatted.
 *
 * @param self: slice of characters to format
 * @param args: arguments to format the string with
 *
 * @return a new string with the formatted string
 */
fn slice::format(@format const self: &slice<const char>, args...) -> own string {
    let str = string();
    let modifier = string();

    let i: uint64 = 0;
    let bracket_open = false;
    let bracket_closed = false;

    for c in self {
        case (c) {
        when '{':
            if (bracket_open) {
                str.push(c);
                bracket_open = false;
                continue;
            }

            bracket_open = true;
        when '}':
            if (bracket_closed) {
                str.push(c);
                bracket_closed = false;
                continue;
            }

            if (!bracket_open) {
                bracket_closed = true;
                continue;
            }

            bracket_open = false;

            if (i < args.length) {
                with (t = args[i] as T) {
                    format(str, t, modifier as slice<char>);
                }

                modifier.reset();
                i += 1;
            }
        else:
            if (bracket_open) {
                modifier.push(c);
            } else {
                str.push(c);
            }
        }
    }

    return move(str);
}

/**
 * Compares a slice against another slice: equal when every element matches
 * and the lengths are the same.
 *
 * @param self: slice to compare
 * @param str:  slice to compare against
 *
 * @return true if the slices are equal, false otherwise
 */
fn slice<T>::equals(const self: &slice<T>, const str: &slice<T>) -> bool {
    if (self.length != str.length)
        return false;

    for i in range(self.length) {
        if (self.data![i] != str.data![i])
            return false;
    }

    return true;
}