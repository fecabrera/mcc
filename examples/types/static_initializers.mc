import "std/io";

// A `@static` global has file-scoped storage and a constant initializer,
// folded at compile time into the binary's data. Beyond scalars, arrays, and
// slices, that initializer may now be a struct or union literal -- so an
// aggregate can live in static storage without a runtime assignment in `main`.

struct point {
    x: int32;
    y: int32;
}

// Every field is a compile-time constant, so the whole struct folds to a data
// constant. Omitted fields follow the literal rules: zero, or a `= default`.
struct config {
    limit: int32 = 100;
    used:  int32;
}

@static let origin: struct point = point { x = 0, y = 0 };
@static let corner: struct point = point { x = 3, y = 4 };
@static let setup:  struct config = config { used = 5 };   // limit defaults to 100

// Nested struct and array fields fold recursively.
struct box {
    corner: struct point;
    sizes:  int32[3];
}

@static let unit: struct box = box {
    corner = point { x = 1, y = 1 },
    sizes  = [10, 20, 30],
};

// A union global folds too. The member the literal writes need not be the
// widest one: the constant is sized to the whole union, the written member's
// bytes up front and the rest zero -- exactly the storage a runtime
// `union num { ... }` literal produces, just resolved at compile time.
union num {
    i: int64;
    b: uint8;
}

@static let whole: union num = num { i = 42 };    // the widest member
@static let byte:  union num = num { b = 200 };   // a narrow member, zero-padded
@static let blank: union num = num { };           // empty: all zeroes

// A struct inside a union works the same way -- the struct constant is the
// union's live member.
union shape {
    at:    struct point;
    bits:  uint64;
}

@static let placed: union shape = shape { at = point { x = 7, y = 8 } };

// Generics monomorphize before the constant is built, so a generic aggregate
// global is initialized like any concrete one.
union boxed<T> {
    typed: T;
    raw:   uint64;
}

@static let held: union boxed<int32> = boxed<int32> { typed = 9 };

fn main() -> int32 {
    // Structs read back field by field; a whole-value copy works too.
    let here = corner;
    println("corner      = ({}, {})", here.x, here.y);
    println("config      = limit {}, used {}", setup.limit, setup.used);
    println("unit box    = corner ({}, {}), depth {}",
            unit.corner.x, unit.corner.y, unit.sizes[2]);

    // Union members reinterpret the shared bytes, folded or not.
    println("whole.i     = {}", whole.i as int32);
    println("byte.b      = {}", byte.b as int32);
    println("blank.i     = {}", blank.i as int32);
    println("placed.at   = ({}, {})", placed.at.x, placed.at.y);
    println("held.typed  = {}", held.typed);

    return 0;
}

// See also: struct_literals.mc for the runtime `Name { ... }` literal these
// globals fold at compile time; unions.mc for member layout and byte
// reinterpretation; any.mc for a `@static` `any` folding its constant
// initializer into a constant tagged box the same way.
