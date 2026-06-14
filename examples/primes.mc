import "libc/stdio";

fn is_prime(n: int32) -> bool {
    if (n < 2) {
        return false;
    }
    let d: int32 = 2;
    while (d * d <= n) {
        if (n % d == 0) {
            return false;
        }
        d = d + 1;
    }
    return true;
}

fn main() -> int32 {
    let count: int32 = 0;
    let n: int32 = 2;
    while (n < 50) {
        if (is_prime(n)) {
            printf("%d ", n);
            count = count + 1;
        }
        n = n + 1;
    }
    printf("\n%d primes below 50\n", count);
    return 0;
}
