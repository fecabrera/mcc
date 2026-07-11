; Highlight queries for mcc (.mc), using Neovim's standard capture names.
; Neovim gives later patterns priority, so the broad catch-alls come first
; and the specific overrides (functions, parameters, builtins) come after.

; ---------------------------------------------------------------- comments
(comment) @comment

; ----------------------------------------------------------------- literals
(number) @number
(float) @number.float
(string) @string
(f_string) @string
(char) @character
(boolean) @boolean
(null) @constant.builtin

; -------------------------------------------------------------- annotations
(annotation_name) @attribute

; ------------------------------------------------- identifiers (catch-alls)
(identifier) @variable
(type_identifier) @type

; Built-in scalar types override the generic type capture above.
((type_identifier) @type.builtin
 (#any-of? @type.builtin
  "int8" "int16" "int32" "int64"
  "uint8" "uint16" "uint32" "uint64"
  "char" "byte" "bool" "float64" "void" "va_list" "any"))

; --------------------------------------------------------------- functions
(function_definition name: (identifier) @function)
(function_prototype name: (identifier) @function)
(call_expression function: (identifier_expression (identifier) @function.call))
(call_expression function: (member_expression field: (identifier) @function.method.call))

; --------------------------------------------------------- vars and members
(parameter name: (identifier) @variable.parameter)
(variadic_parameter) @variable.parameter
(field_list name: (identifier) @variable.member)
(member_expression field: (identifier) @variable.member)

; Enum members are named compile-time constants; error variants likewise.
(enum_member name: (identifier) @constant)
(enum_access member: (identifier) @constant)
(error_member name: (identifier) @constant)

; ----------------------------------------------------------------- keywords
"fn" @keyword.function

[
  "let"
  "defer"
  "in"
  "unreachable"
] @keyword

[
  "const"
  "mut"
] @keyword.modifier

[
  "struct"
  "union"
  "enum"
  "error"
  "type"
  "extends"
] @keyword.type

"import" @keyword.import

[
  "return"
  "emit"
] @keyword.return

[
  "if"
  "else"
  "case"
  "when"
  "with"
  "try"
  "except"
] @keyword.conditional

[
  "while"
  "until"
  "for"
  "break"
  "continue"
] @keyword.repeat

[
  "and"
  "or"
  "as"
] @keyword.operator

; sizeof / alignof / offsetof / typename / len read as built-in calls.
[
  "sizeof"
  "alignof"
  "offsetof"
  "typename"
  "len"
] @function.builtin

; ---------------------------------------------------------------- operators
[
  "->"
  "=="
  "!="
  "<="
  ">="
  "<<"
  ">>"
  "+="
  "-="
  "*="
  "/="
  "%="
  "&="
  "|="
  "^="
  "<<="
  ">>="
  "="
  "+"
  "-"
  "*"
  "/"
  "%"
  "!"
  "&"
  "|"
  "^"
  "~"
  "<"
  ">"
  "."
  "?"
  "::"
] @operator

; ------------------------------------------------------------- punctuation
[
  "("
  ")"
  "{"
  "}"
  "["
  "]"
] @punctuation.bracket

[
  ";"
  ","
  ":"
] @punctuation.delimiter

; ------------------------------------------------------ f-string interiors
; The (f_string) capture above paints the frame and literal text; hole
; expressions inside carry their own captures naturally. These overrides
; come last (Neovim gives later patterns priority) so the hole delimiters
; and the inspector `=` win their tokens back from the generic
; operator/punctuation captures.
(escape_sequence) @string.escape
(interpolation ["{" "}"] @punctuation.special)
(interpolation "=" @punctuation.special)
(interpolation ":" @punctuation.special)
(format_spec) @string.special
