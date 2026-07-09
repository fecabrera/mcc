import "std/io";

// A `case type` arm may list several comma-separated types over one binding:
// `when int32, int16, int8 n:` is one arm, three tags, one shared body. The
// binding is an implicit generic: the body compiles once per listed type with
// `n` typed as that copy's concrete type (never a union), and each copy is
// fully type-checked on its own. The motivating shape is formatter-style
// grouping: every signed width shares one %d body, every unsigned width one
// %u body, instead of six near-identical arms.
// Prerequisites: any.mc for the box and the single-type form of `case type`;
// functions/overloading.mc for the overload set resolved below.

// An overload set with one member per signed width. If a listed type had no
// viable member (say, no width(int8)), that copy of the shared body would
// fail to compile, with a note naming the offending type:
// `in case type arm for int8`.
fn width(n: int32) -> int32 { return 4; }
fn width(n: int16) -> int32 { return 2; }
fn width(n: int8)  -> int32 { return 1; }

fn describe(a: any) {
    case type (a) {
        // The distinctive consequence of per-copy compilation: the width(n)
        // call resolves against each copy's concrete binding type, so it
        // picks width(int32) in the int32 copy, width(int16) in the int16
        // copy, and width(int8) in the int8 copy. The `as int32` cast rides
        // the C default argument promotions into varargs printf's %d.
        when int32, int16, int8 n:
            println("signed   {} (width {})", n as int32, width(n));

        // The unsigned group shares a %u body. A type may appear once across
        // the whole switch: listing uint16 again, here or in another arm,
        // is the compile error `duplicate case type arm for uint16`.
        when uint32, uint16, uint8 u:
            println("unsigned {}", u as uint32);

        // A list does not close the universe, so `else` is still mandatory.
        else:
            println("not a grouped integer");
    }
}

fn main() -> int32 {
    // One value per tag: each matches its group's arm and runs the copy of
    // the shared body compiled for its type.
    let h: int16 = -12;
    let b: int8 = 3;
    describe(300);   // untyped literal anchors at int32
    describe(h);
    describe(b);

    let big: uint32 = 4000000000;   // fits uint32, not int32
    let s: uint16 = 9;
    describe(big);
    describe(s);

    // A type no group lists lands in the mandatory else.
    describe(2.5);

    return 0;
}

// See also: any.mc for the box itself and the single-type arms this extends;
// generic_case_arms.mc for the fully generic arms, `when T v:` and
// `when T* ptr:`, that this explicit grouping is the halfway point to;
// functions/overloading.mc for how an overload set like width is declared
// and resolved; type_groups.mc for the function-declaration counterpart,
// closed type groups partitioning an overload set with no box and no
// `case type` needed.
