// Bindings for the C <math.h> double-precision functions. mcc's only floating
// type is float64 (C double), so the float/long-double variants (sinf, sinl,
// ...) are omitted. Link with -lm (the driver does this automatically).

/***************************************
 * Trigonometric
 ***************************************/

/** Sine of x, with x in radians. @param x: angle in radians @return sin(x) */
@extern fn sin(x: float64) -> float64;

/** Cosine of x, with x in radians. @param x: angle in radians @return cos(x) */
@extern fn cos(x: float64) -> float64;

/** Tangent of x, with x in radians. @param x: angle in radians @return tan(x) */
@extern fn tan(x: float64) -> float64;

/** Arc sine of x. @param x: value in [-1, 1] @return angle in radians, in [-pi/2, pi/2] */
@extern fn asin(x: float64) -> float64;

/** Arc cosine of x. @param x: value in [-1, 1] @return angle in radians, in [0, pi] */
@extern fn acos(x: float64) -> float64;

/** Arc tangent of x. @param x: value @return angle in radians, in [-pi/2, pi/2] */
@extern fn atan(x: float64) -> float64;

/**
 * Arc tangent of y/x, using the signs of both to select the quadrant.
 *
 * @param y: numerator
 * @param x: denominator
 *
 * @return angle in radians, in [-pi, pi]
 */
@extern fn atan2(y: float64, x: float64) -> float64;

/***************************************
 * Hyperbolic
 ***************************************/

/** Hyperbolic sine of x. @param x: value @return sinh(x) */
@extern fn sinh(x: float64) -> float64;

/** Hyperbolic cosine of x. @param x: value @return cosh(x) */
@extern fn cosh(x: float64) -> float64;

/** Hyperbolic tangent of x. @param x: value @return tanh(x) */
@extern fn tanh(x: float64) -> float64;

/** Inverse hyperbolic sine of x. @param x: value @return asinh(x) */
@extern fn asinh(x: float64) -> float64;

/** Inverse hyperbolic cosine of x. @param x: value >= 1 @return acosh(x) */
@extern fn acosh(x: float64) -> float64;

/** Inverse hyperbolic tangent of x. @param x: value in (-1, 1) @return atanh(x) */
@extern fn atanh(x: float64) -> float64;

/***************************************
 * Exponential and logarithmic
 ***************************************/

/** e raised to the power x. @param x: exponent @return e**x */
@extern fn exp(x: float64) -> float64;

/** 2 raised to the power x. @param x: exponent @return 2**x */
@extern fn exp2(x: float64) -> float64;

/** e**x - 1, accurate for small x. @param x: exponent @return e**x - 1 */
@extern fn expm1(x: float64) -> float64;

/** Natural (base-e) logarithm of x. @param x: value > 0 @return ln(x) */
@extern fn log(x: float64) -> float64;

/** Base-10 logarithm of x. @param x: value > 0 @return log10(x) */
@extern fn log10(x: float64) -> float64;

/** Base-2 logarithm of x. @param x: value > 0 @return log2(x) */
@extern fn log2(x: float64) -> float64;

/** ln(1 + x), accurate for small x. @param x: value > -1 @return ln(1 + x) */
@extern fn log1p(x: float64) -> float64;

/** Radix-independent exponent of x as a float. @param x: value @return floor(log2(|x|)) */
@extern fn logb(x: float64) -> float64;

/** Radix-independent exponent of x as an int. @param x: value @return the exponent */
@extern fn ilogb(x: float64) -> int32;

/**
 * Splits x into a normalized fraction in [0.5, 1) and a power of two.
 *
 * @param x:   value to split
 * @param exp: written with the exponent
 *
 * @return the fraction, such that x == fraction * 2**exp
 */
@extern fn frexp(x: float64, exp: int32*) -> float64;

/** x * 2**exp. @param x: significand @param exp: power of two @return x * 2**exp */
@extern fn ldexp(x: float64, exp: int32) -> float64;

/**
 * Splits x into integral and fractional parts, each with x's sign.
 *
 * @param x:    value to split
 * @param iptr: written with the integral part
 *
 * @return the fractional part
 */
@extern fn modf(x: float64, iptr: float64*) -> float64;

/** x * 2**n (like ldexp). @param x: significand @param n: power of two @return x * 2**n */
@extern fn scalbn(x: float64, n: int32) -> float64;

/***************************************
 * Power and absolute value
 ***************************************/

/** base raised to exp. @param base: base @param exp: exponent @return base**exp */
@extern fn pow(base: float64, exp: float64) -> float64;

/** Square root of x. @param x: value >= 0 @return sqrt(x) */
@extern fn sqrt(x: float64) -> float64;

/** Cube root of x. @param x: value @return cbrt(x) */
@extern fn cbrt(x: float64) -> float64;

/** sqrt(x*x + y*y), without undue overflow. @param x: leg @param y: leg @return the hypotenuse */
@extern fn hypot(x: float64, y: float64) -> float64;

/** Absolute value of x. @param x: value @return |x| */
@extern fn fabs(x: float64) -> float64;

/***************************************
 * Error and gamma
 ***************************************/

/** Error function of x. @param x: value @return erf(x) */
@extern fn erf(x: float64) -> float64;

/** Complementary error function, 1 - erf(x). @param x: value @return erfc(x) */
@extern fn erfc(x: float64) -> float64;

/** Natural log of the absolute value of the gamma function. @param x: value @return ln|gamma(x)| */
@extern fn lgamma(x: float64) -> float64;

/** Gamma function of x. @param x: value @return gamma(x) */
@extern fn tgamma(x: float64) -> float64;

/***************************************
 * Nearest integer
 ***************************************/

/** Smallest integral value not less than x. @param x: value @return ceil(x) */
@extern fn ceil(x: float64) -> float64;

/** Largest integral value not greater than x. @param x: value @return floor(x) */
@extern fn floor(x: float64) -> float64;

/** x rounded toward zero to an integral value. @param x: value @return trunc(x) */
@extern fn trunc(x: float64) -> float64;

/** x rounded to the nearest integral value, halves away from zero. @param x: value @return round(x) */
@extern fn round(x: float64) -> float64;

/** x rounded to an integral value using the current rounding mode, no inexact exception. @param x: value @return nearbyint(x) */
@extern fn nearbyint(x: float64) -> float64;

/** Like nearbyint, but may raise the inexact exception. @param x: value @return rint(x) */
@extern fn rint(x: float64) -> float64;

/** x rounded to the nearest integer, halves away from zero. @param x: value @return the rounded value */
@extern fn lround(x: float64) -> int64;

/** Like lround. @param x: value @return the rounded value */
@extern fn llround(x: float64) -> int64;

/** x rounded to an integer using the current rounding mode. @param x: value @return the rounded value */
@extern fn lrint(x: float64) -> int64;

/** Like lrint. @param x: value @return the rounded value */
@extern fn llrint(x: float64) -> int64;

/***************************************
 * Remainder
 ***************************************/

/** Floating-point remainder of x/y (sign of x). @param x: dividend @param y: divisor @return x - n*y, n truncated */
@extern fn fmod(x: float64, y: float64) -> float64;

/** IEEE remainder of x/y (n rounded to nearest). @param x: dividend @param y: divisor @return the remainder */
@extern fn remainder(x: float64, y: float64) -> float64;

/**
 * Like remainder, but also returns the low bits of the quotient.
 *
 * @param x:   dividend
 * @param y:   divisor
 * @param quo: written with the low-order bits of the quotient x/y
 *
 * @return the remainder
 */
@extern fn remquo(x: float64, y: float64, quo: int32*) -> float64;

/***************************************
 * Floating-point manipulation
 ***************************************/

/** Magnitude of x with the sign of y. @param x: magnitude source @param y: sign source @return copysign(x, y) */
@extern fn copysign(x: float64, y: float64) -> float64;

/** Next representable value after x toward y. @param x: starting value @param y: direction @return the next value */
@extern fn nextafter(x: float64, y: float64) -> float64;

/** A quiet NaN, with an implementation-defined payload from tagp. @param tagp: payload string @return NaN */
@extern fn nan(tagp: uint8*) -> float64;

/***************************************
 * Maximum, minimum, positive difference
 ***************************************/

/** Positive difference: max(x - y, 0). @param x: value @param y: value @return x - y if x > y, else +0 */
@extern fn fdim(x: float64, y: float64) -> float64;

/** Larger of x and y, ignoring NaN where possible. @param x: value @param y: value @return the maximum */
@extern fn fmax(x: float64, y: float64) -> float64;

/** Smaller of x and y, ignoring NaN where possible. @param x: value @param y: value @return the minimum */
@extern fn fmin(x: float64, y: float64) -> float64;

/***************************************
 * Fused multiply-add
 ***************************************/

/** x*y + z computed with a single rounding. @param x: factor @param y: factor @param z: addend @return x*y + z */
@extern fn fma(x: float64, y: float64, z: float64) -> float64;
