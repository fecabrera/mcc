#include <stdio.h>

fn main() -> int32 {
    // printf supports the usual C format specifiers.
    printf("int:      %d\n", -42);
    printf("unsigned: %u\n", 42);
    printf("char:     %c\n", 65);          // 'A'
    printf("float:    %f\n", 3.14159);
    printf("percent:  %%\n");
    printf("width:    [%5d]\n", 42);
    printf("several:  %d %d %d\n", 1, 2, 3);

    // puts writes a string and adds a newline.
    puts("puts adds a newline");

    // putchar writes a single character by code point.
    putchar(104);  // 'h'
    putchar(105);  // 'i'
    putchar(10);   // '\n'

    // String literals support \n \t \r \0 \" \\ escapes.
    printf("tab:\there\nquote: \"quoted\"\nbackslash: \\\n");

    // getchar() -> int32 reads one byte from stdin (not called here so this
    // example runs without input).
    return 0;
}
