import "std/io";

// Assigning a string literal to an existing char-slice lvalue: `s = "hi";`
// reborrows with no explicit `as`. It repoints the slice at the literal's
// global string constant, dropping the NUL, so `.length` becomes the new
// literal's text length. This is the last position in the string/array-literal
// adaptation family, joining the `let`, argument, element, and struct-field
// positions the examples below cover.
//
// A string constant has static lifetime, so the reborrow stays valid even when
// the target outlives the current frame. That is what lets assignment reach
// every lvalue form, including ones an array literal cannot (see the closing
// note): a plain name, a member, a deref, an index, and a mut return.
//
// Prerequisites: the slice<T> view and borrowing (memory/slices.mc) and string
// literals with their NUL-dropping borrow (types/strings.mc).
// See also: memory/slice_literals.mc (array literals borrowing into slices, and
// why array-literal assignment is rejected), types/struct_literals.mc (the
// `cmd { name = "hi" }` struct-literal field this mirrors), and
// types/string_tables.mc (string-literal elements in a lookup table).

// A struct with a char-slice field, for the member-assignment form.
struct command { name: slice<const char>; }

// An out-parameter, for the deref form: writing through `*out` reborrows the
// caller's slice.
fn set_label(out: slice<const char>*) {
    *out = "labelled";
}

// A mut-return accessor, for the mut-return form: its call is an assignable
// lvalue. Array parameters cannot be `mut`, so the run is passed by pointer.
fn slot(rows: slice<char>*, i: int32) -> mut slice<char> {
    return rows[i];
}

// A static char-slice global starts as one borrow and is reassignable at
// runtime to another string constant.
@static let banner: slice<const char> = "boot";

fn main() -> int32 {
    // Form 1, a plain name. The binding is a slice<char>; `= "hello"` repoints
    // it, and .length tracks the new literal (2, then 5). Locals are mutable by
    // default, so no extra keyword is needed to reassign.
    let s: slice<char> = "hi";
    writestr(s); println(" (length %llu)", s.length);   // hi (length 2)
    s = "hello";
    writestr(s); println(" (length %llu)", s.length);   // hello (length 5)

    // Form 2, a member. This is the headline case: it closes the gap where the
    // struct literal `command { name = "…" }` adapted but the field assignment
    // did not. The field is slice<const char>, and `c.name = "listing"` borrows
    // into it exactly as the literal form does.
    let c: struct command;
    c.name = "listing";
    writestr(c.name); println(" (length %llu)", c.name.length);   // listing (length 7)

    // Form 3, a deref. set_label writes `*out = "labelled";` through the
    // pointer, reborrowing the caller's slice even though `label` outlives the
    // callee's frame -- safe because the string constant is static.
    let label: slice<const char> = "unset";
    set_label(&label);
    writestr(label); println(" (length %llu)", label.length);     // labelled (length 8)

    // Form 4, an index. Each element of a char-slice array takes a literal by
    // assignment, the same borrow the initializer list in string_tables.mc does.
    let rows: slice<char>[2];
    rows[0] = "first";
    rows[1] = "row";
    writestr(rows[0]); writechar(' '); writeln(rows[1]);          // first row

    // Form 5, a mut return. slot(rows, 1) is an lvalue, so it is assignable; the
    // reborrow lands in rows[1].
    slot(rows, 1) = "second";
    writeln(rows[1]);                                             // second

    // A @static char-slice global reassigns at runtime the same way.
    banner = "ready";
    writestr(banner); println(" (length %llu)", banner.length);  // ready (length 5)

    // A ternary of string literals adapts arm by arm, each arm keeping its own
    // length. Here the true arm wins, so s becomes the 3-char "yes".
    let verbose = true;
    s = verbose ? "yes" : "no";
    writestr(s); println(" (length %llu)", s.length);            // yes (length 3)

    // Only string literals adapt in assignment. An ARRAY-literal assignment
    // like `rows[0] = ['x', 'y'];` or `nums = [1, 2, 3];` is a compile error:
    // its backing array is frame-local, but an assignment target can outlive
    // the frame, so the borrowed view would dangle. A string constant is static
    // and has no such hazard. See memory/slice_literals.mc for that contrast.
    return 0;
}
