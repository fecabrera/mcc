import "std/io";

fn is_prime(n: int32) -> bool {
    if (n < 2) {
        return false;
    }
    let d: int32 = 2;
    while (d * d <= n) {
        if (n % d == 0) {
            return false;
        }
        d += 1;
    }
    return true;
}

fn main() -> int32 {
    let count: int32 = 0;
    let n: int32 = 2;
    while (n < 50) {
        if (is_prime(n)) {
            print("%d ", n);
            count += 1;
        }
        n += 1;
    }
    print("\n%d primes below 50\n", count);
    return 0;
}
