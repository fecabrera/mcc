; Highlight queries for mcc (.mc), using Neovim's standard capture names.
; Neovim gives later patterns priority, so the broad catch-alls come first
; and the specific overrides (functions, parameters, builtins) come after.

; ---------------------------------------------------------------- comments
(comment) @comment

; ----------------------------------------------------------------- literals
(number) @number
(float) @number.float
(string) @string
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
  "char" "byte" "bool" "float64" "void" "va_list"))

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

; Enum members are named compile-time constants.
(enum_member name: (identifier) @constant)
(enum_access member: (identifier) @constant)

; ----------------------------------------------------------------- keywords
"fn" @keyword.function

[
  "let"
  "defer"
  "in"
] @keyword

[
  "const"
  "mut"
] @keyword.modifier

[
  "struct"
  "union"
  "enum"
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

; sizeof / alignof / offsetof / len read as built-in calls.
[
  "sizeof"
  "alignof"
  "offsetof"
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
