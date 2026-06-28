/***************************************
 * Floating-point characteristics
 ***************************************/

// Limits of the `double` type (mcc's float64). The single-precision (FLT_*)
// and long-double (LDBL_*) sets are omitted: mcc has no float32 or long double.

const FLT_RADIX = 2;   // base of the floating-point representation

const DBL_MANT_DIG = 53;     // bits of mantissa
const DBL_DIG = 15;          // decimal digits guaranteed round-trippable
const DBL_MIN_EXP = -1021;   // least n such that FLT_RADIX^(n-1) is normalized
const DBL_MAX_EXP = 1024;    // greatest n such that FLT_RADIX^(n-1) is finite
const DBL_MIN_10_EXP = -307; // least n such that 10^n is normalized
const DBL_MAX_10_EXP = 308;  // greatest n such that 10^n is finite

// Largest finite double.
const DBL_MAX = 1.7976931348623157e+308;
// Smallest positive normalized double.
const DBL_MIN = 2.2250738585072014e-308;
// Smallest positive denormalized double.
const DBL_TRUE_MIN = 4.9406564584124654e-324;
// Difference between 1.0 and the next representable double.
const DBL_EPSILON = 2.220446049250313e-16;

// Decimal digits needed to represent any double without loss.
const DECIMAL_DIG = 17;
