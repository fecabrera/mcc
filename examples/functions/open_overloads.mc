import "string";
import "libc/stdio";

// Open overload sets: any module may add overloads to a name declared
// elsewhere, with no opt-in marker. The whole-program set is the union of
// every module's members at import merge, in any import order, and a call
// resolves against that union under the unchanged rules (see
// mixed_overloads.mc for the rank). This file joins the stdlib's
// `string_append` set: string.mc declares three concrete members (a
// slice<char>, a char* with a length, a NUL-terminated char*), and this
// module, a different file, makes its own struct appendable by adding one
// more. Before sets were open, this exact join was rejected as a duplicate
// definition of 'string_append'; now declaring the overload is joining.
// The planned stdlib format protocol (a `format` set every module will add
// its own types to) is the driving use case for this openness.
// Builds on overloading.mc (concrete sets) and mixed_overloads.mc (mixed
// sets and their rank). Uses the string type and struct literals, covered
// later in the tour (memory/lists.mc, types/struct_literals.mc).

struct point {
    x: int32;
    y: int32;
}

// The cross-module join: one concrete overload, declared here, becomes a
// member of string.mc's `string_append` set. Same name, same receiver
// shape as the stdlib members; the value parameter's type is what selects
// it. No annotation, no registration: declaring it is joining.
fn string_append(mut str: string, const value: struct point) {
    let buf: char[32];
    let n = snprintf(buf, 32, "(%d, %d)", value.x, value.y);
    // Appending the rendered text re-enters the same whole-program set and
    // lands on string.mc's (char*, length) member: two modules' members,
    // one set, resolving side by side.
    string_append(str, buf, n as uint64);
}

fn main() -> int32 {
    let line: string;
    string_init(line);
    defer string_destroy(line);

    let p = point { x = 10, y = 31 };

    // Two calls, two members, two defining modules: the literal borrows to
    // string.mc's slice<char> member, and `p` selects the point member
    // declared above.
    string_append(line, "origin moved to ");
    string_append(line, p);

    // A string is a growable list<char> ({data, length}); printf's %.*s
    // prints exactly length bytes of it.
    printf("%.*s\n", line.length as int32, line.data);

    return 0;
}

// The gates on an open set run cross-module too: a second
// string_append(string, point) in yet another module is the same
// duplicate-definition error as within one file, with a note citing this
// member's site. Resolution never silently rewires: adding an import can
// only add candidates or collide loudly.

// See also: overloading.mc (concrete sets in one module and the
// must-differ rule), mixed_overloads.mc (generic and concrete members
// sharing a set, the concrete > bounded > unbounded rank), mut_overloads.mc
// (resolution inside a generic set). Full rules: docs/language.md,
// "Function overloading".
