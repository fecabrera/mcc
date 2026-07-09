import "std/io";

// Forward declarations: a bodyless `fn` prototype coexisting with its
// definition in the same program. The prototype states the signature up
// front; the matching definition later supplies the body.
// Prerequisites: functions.mc.
//
// mcc never *needs* a forward declaration for ordering: all signatures are
// collected in a first pass, so any-order definitions already work with no
// prototypes at all (see functions.mc, where main calls everything below
// it). The point here is that a prototype and the definition may now legally
// meet. That happens naturally when a build imports a module's .mci
// interface stub (a file of prototypes, see systems/interfaces.mc) while
// also compiling the module's .mc source; and it permits a C-header-style
// block like the one below, keeping a file's surface readable at the top.

// The "header": this file's functions, declared up front. A prototype ends
// with `;` instead of a body.
fn clamp(v: int32, lo: int32, hi: int32) -> int32;
fn celsius_to_fahrenheit(c: float64) -> float64;

// Two identical prototypes collapse onto one declaration (like repeated
// @extern declarations), so a hand-written prototype and the same one
// arriving from an imported .mci do not collide either.
fn clamp(v: int32, lo: int32, hi: int32) -> int32;

// A prototype pairs with a definition per SIGNATURE: one with a different
// parameter list is not a mismatch, it declares another member of the
// name's overload set (see overloading.mc) and needs its own definition.

fn main() -> int32 {
    println("clamp(17, 0, 10) = {}", clamp(17, 0, 10));
    println("100 C = {} F", celsius_to_fahrenheit(100.0));
    return 0;
}

// The definitions. Each is checked against its prototype, then the body is
// generated into the already-declared function. Parameter names may differ
// (`value` here vs `v` above), but the resolved signature must match
// exactly; changing a type (say `lo: int64`) fails at declaration time with
// the compile error `definition of 'clamp' does not match its prototype`.
fn clamp(value: int32, lo: int32, hi: int32) -> int32 {
    if (value < lo) { return lo; }
    if (value > hi) { return hi; }
    return value;
}

fn celsius_to_fahrenheit(c: float64) -> float64 {
    return c * 9.0 / 5.0 + 32.0;
}

// See also: functions.mc (any-order definitions, no prototype needed),
// systems/interfaces.mc (.mci interface stubs, where prototypes come from).
