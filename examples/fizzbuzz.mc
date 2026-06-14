import "std";

fn main() -> int32 {
    let i: int32 = 1;
    while (i <= 20) {
        if (i % 15 == 0) {
            println("FizzBuzz");
        } else if (i % 3 == 0) {
            println("Fizz");
        } else if (i % 5 == 0) {
            println("Buzz");
        } else {
            println("%d", i);
        }
        i = i + 1;
    }
    return 0;
}
