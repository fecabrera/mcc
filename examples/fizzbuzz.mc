import "std";
import "range";

fn main() -> int32 {
    let r = struct range<int32> { start = 1, end = 21 };
    for i in &r {
        if (i % 15 == 0) {
            println("FizzBuzz");
        } else if (i % 3 == 0) {
            println("Fizz");
        } else if (i % 5 == 0) {
            println("Buzz");
        } else {
            println("%d", i);
        }
    }
    return 0;
}
