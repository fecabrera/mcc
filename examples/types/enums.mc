import "std/io";

// An `enum` is a named set of compile-time constants. Each member has an
// explicit value; the optional `: <type>` after the name is the underlying
// type (defaulting to int32). A member is read as `Enum::Member` and folds to
// a constant of the underlying type -- no storage is emitted.
enum Color: int32 {
    Red   = 0,
    Green = 1,
    Blue  = 2,
}

// The enum's name is also a type, aliasing the underlying type. It can annotate
// parameters, returns, variables, struct fields, and arrays.
fn name_of(c: Color) -> char* {
    case (c) {
        when Color::Red:   return "red";
        when Color::Green: return "green";
        else:              return "blue";
    }
}

// The underlying type may be any type, and a member's value any constant
// expression that resolves to it. Here a uint64 flag set built with shifts...
enum Flags: uint64 {
    None = 0,
    A    = 1 << 0,
    B    = 1 << 1,
    High = 1 << 40,
}

// ...and an enum whose members are strings.
enum Msg: char* {
    Hi  = "hello",
    Bye = "goodbye",
}

// A member may reference an earlier member of the same enum, or any constant
// already in scope.
enum Step: int32 {
    First  = 1,
    Second = Step::First + 1,
    Third  = Step::Second + 1,
}

// An enum type works as a struct field like any other type.
struct pixel {
    color: Color;
    alpha: uint8;
}

fn main() -> int32 {
    // A member folds to its underlying value.
    println("Color::Green = {}", Color::Green);

    // The enum used as a parameter type.
    println("name_of(Blue) = {}", name_of(Color::Blue));

    // An array typed by the enum.
    let palette: Color[3] = [Color::Red, Color::Green, Color::Blue];
    let i: int32 = 0;
    while (i < 3) {
        println("palette[{}] = {}", i, name_of(palette[i]));
        i += 1;
    }

    // uint64 flags, combined with bitwise OR and tested with AND.
    let perms: Flags = (Flags::A | Flags::B) as Flags;
    if ((perms & Flags::A) != Flags::None) {
        println("flag A is set; High = {}", Flags::High);
    }

    // String-valued members.
    println("Msg::Hi = {}, Msg::Bye = {}", Msg::Hi, Msg::Bye);

    // A member defined in terms of an earlier one.
    println("Step::Third = {}", Step::Third);

    // The enum as a struct field.
    let p: struct pixel;
    p.color = Color::Green;
    p.alpha = 255;
    println("pixel is {}, alpha {}", name_of(p.color), p.alpha);

    return 0;
}

// See also: derived_enums.mc for reusing one enum's members in another
// (`enum b: a` copies a's members and adopts its underlying type), and
// error_handling.mc for the `error` declaration, the enum's nominal sibling
// for failure causes (auto-numbered, no arithmetic, no implicit integers).
