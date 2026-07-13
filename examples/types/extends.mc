import "std/io";

// Named-base `extends`: `struct point3 extends point` reuses the base's
// layout as the derived struct's PREFIX. The base's fields are laid out
// first, then the derived struct's own, so every derived value starts with
// a complete base and upcasting just reads the front of it.
//
// The subtype relation is NOMINAL: the declared `extends` lineage decides
// whether an upcast is allowed, not the layout. A struct that merely shares
// point's field prefix but does not `extends point` won't upcast to it, and
// the sibling specializations below (both `extends scalar`) never interconvert.
// Prerequisites: structs.mc; struct_literals.mc for the `= default` fields.

// The base, and a derived struct laid out x, y, z.
struct point  { x: int32; y: int32; }
struct point3 extends point { z: int32; }

// A function over the base. It knows nothing about point3.
fn length2(p: struct point*) -> int32 {
    return p!->x * p!->x + p!->y * p!->y;
}

// Defaults travel with the fields: a derived literal fills omitted base
// fields from the base's defaults, alongside its own.
struct config {
    capacity: int32 = 16;
    verbose:  int32;              // no default, zero when omitted
}
struct db_config extends config {
    port: int32 = 5432;
}

// A bodyless extender is a SPECIALIZATION: a distinct type with the base's
// exact layout, for branding values the compiler must keep apart.
struct scalar { value: int32; }
struct meters extends scalar;
struct feet   extends scalar;

fn double_meters(m: struct meters) -> struct meters {
    return meters { value = m.value * 2 };
}

fn main() -> int32 {
    // Inherited fields are the derived struct's own: name them in a
    // literal and read them with `.` directly, no base member in between.
    let p = point3 { x = 3, y = 4, z = 5 };
    println("p = ({}, {}, {})", p.x, p.y, p.z);

    // The base is a true prefix: x and y sit at the front, z after them.
    println("offsetof(point3, z) = {}, sizeof(point3) = {}",
            offsetof(struct point3, z), sizeof(struct point3));

    // Pointer upcast: `&p as struct point*` reads the same storage, so a
    // base-only function accepts the derived value. The cast is explicit;
    // a point3* is a distinct type and never silently passes as point*.
    println("length2 = {}", length2(&p as struct point*));

    // Value upcast copies just the base prefix, dropping z. Only this
    // direction exists: narrowing a point back to a point3 would have to
    // read past the base, so it is a compile error.
    let q = p as struct point;
    println("q = ({}, {})", q.x, q.y);

    // Base defaults carry down: capacity comes from config's default, port
    // from db_config's own, verbose has no default and stays zero.
    let c = db_config { };
    println("capacity = {}, verbose = {}, port = {}",
            c.capacity, c.verbose, c.port);

    // The specializations share scalar's exact layout...
    println("sizeof: scalar {}, meters {}, feet {}",
            sizeof(struct scalar), sizeof(struct meters), sizeof(struct feet));
    // ...but stay distinct types: passing a feet value here fails with
    // "argument 1 of 'double_meters': expected meters, got feet".
    let m = double_meters(meters { value = 21 });
    println("doubled = {} meters", m.value);

    return 0;
}

// See also: generic_extends.mc for a generic struct extending a generic
// base; method_inheritance.mc for what rides on this lineage besides the
// fields: the base's method and constructor families, callable on the
// derived type; memory/intrusive_list.mc for the bare-parameter form
// `extends T`, where the base is whatever payload the struct is
// instantiated with.
