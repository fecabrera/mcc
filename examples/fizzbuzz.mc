import "libc/stdio";

fn main() -> int32 {
    let i: int32 = 1;
    while (i <= 20) {
        if (i % 15 == 0) {
            puts("FizzBuzz");
        } else if (i % 3 == 0) {
            puts("Fizz");
        } else if (i % 5 == 0) {
            puts("Buzz");
        } else {
            printf("%d\n", i);
        }
        i = i + 1;
    }
    return 0;
}
