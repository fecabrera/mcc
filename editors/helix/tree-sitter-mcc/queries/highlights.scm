; Highlight queries for mcc (.mc). Scope names follow Helix's theme keys.
; Helix resolves overlapping captures last-wins, so the broad catch-alls come
; first and the specific overrides (functions, parameters, builtins) come after.

; ---------------------------------------------------------------- comments
(comment) @comment

; ----------------------------------------------------------------- literals
(number) @constant.numeric.integer
(float) @constant.numeric.float
(string) @string
(f_string) @string
(char) @constant.character
(boolean) @constant.builtin.boolean
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
(call_expression function: (member_expression field: (identifier) @function.method))

; `move(...)` is the ownership-transfer operator (an ordinary identifier
; elsewhere), so it is highlighted only as a call callee. Comes after the
; @function.call capture above so last-wins gives it the operator color.
((call_expression function: (identifier_expression (identifier) @keyword.operator))
 (#eq? @keyword.operator "move"))

; --------------------------------------------------------- vars and members
(parameter name: (identifier) @variable.parameter)
(variadic_parameter) @variable.parameter
(field_list name: (identifier) @variable.other.member)
(member_expression field: (identifier) @variable.other.member)

; Enum members are named compile-time constants; error variants likewise.
(enum_member name: (identifier) @constant)
(enum_access member: (identifier) @constant)
(error_member name: (identifier) @constant)

; ----------------------------------------------------------------- keywords
[
  "fn"
  "let"
  "const"
  "own"
  "struct"
  "union"
  "enum"
  "error"
  "type"
  "extends"
] @keyword

"import" @keyword.control.import
"return" @keyword.control.return
"emit" @keyword.control.return
"defer" @keyword.control
"unreachable" @keyword.control

[
  "if"
  "else"
  "case"
  "when"
  "with"
  "try"
  "except"
] @keyword.control.conditional

[
  "while"
  "until"
  "for"
  "break"
  "continue"
] @keyword.control.repeat

"in" @keyword.control

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
  "??"
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
; come last so the hole delimiters and the inspector `=` win their tokens
; back from the generic operator/punctuation captures.
(escape_sequence) @constant.character.escape
(interpolation ["{" "}"] @punctuation.special)
(interpolation "=" @punctuation.special)
(interpolation ":" @punctuation.special)
(format_spec) @string.special
