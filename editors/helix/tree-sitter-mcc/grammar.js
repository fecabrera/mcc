/**
 * @file Tree-sitter grammar for mcc (.mc) -- a small, modern-C-style language.
 * @see https://github.com/fecabrera/mcc
 *
 * Mirrors the hand-written recursive-descent parser in mcc/parser.py. It is a
 * highlighting grammar: it follows the real syntax closely but does not enforce
 * semantic rules the compiler checks later (e.g. where an annotation may apply).
 */

const PREC = {
  ternary: 0,
  or: 1,
  and: 2,
  equality: 3,
  relational: 4,
  bitor: 5,
  bitxor: 6,
  bitand: 7,
  shift: 8,
  add: 9,
  mul: 10,
  as: 11,
  unary: 12,
  postfix: 13,
  call: 14,
};

const commaSep = (rule) => optional(commaSep1(rule));
const commaSep1 = (rule) => seq(rule, repeat(seq(',', rule)));

module.exports = grammar({
  name: 'mcc',

  word: ($) => $.identifier,

  extras: ($) => [/\s/, $.comment],

  conflicts: ($) => [
    // `name < ...` is a comparison until a `>` followed by `(` proves it was a
    // generic call; let the GLR parser explore both.
    [$.identifier_expression, $._type_name],
    [$.binary_expression, $.call_expression],
    [$.binary_expression, $.unary_expression, $.call_expression],
    // `{ ... }` opening a statement is a block, not a block-expression used as
    // an expression statement; the dynamic precedence below settles it.
    [$.block, $.block_expression],
    // A `@if` body holds declarations at the top level and statements inside a
    // function; an empty `{}` matches both, so let GLR choose by content. A
    // nested `@if` (a `conditional`) is the one item both bodies share.
    [$.declaration_block, $.block],
    [$._declaration, $._statement],
    [$.global_variable, $.let_statement],
    // A trailing `(...)`/`[...]` binds to the preceding expression as a call or
    // index (maximal munch), rather than starting a new braceless-body
    // statement; prefer the postfix reading.
    [$.argument_list, $.parenthesized_expression],
    // `Name {` may open a struct literal or be an expression followed by a
    // block statement; the literal's dynamic precedence settles it.
    [$.struct_literal, $.identifier_expression],
    [$.struct_literal, $.identifier_expression, $._type_name],
    // `x as T * ...`: the `*` is a pointer star (`x as int32*`) or a
    // multiplication (`x as uint64 * 4`); fork and let the surviving reading
    // win (pointer_type's dynamic precedence breaks genuine ties toward
    // multiplication, matching the compiler's lookahead rule).
    [$.pointer_type, $.cast_expression],
  ],

  rules: {
    source_file: ($) => seq(repeat($.import), repeat($._declaration)),

    import: ($) => seq('import', field('path', $.string), ';'),

    // ---------------------------------------------------------------- comments
    comment: ($) =>
      token(
        choice(seq('//', /[^\n]*/), seq('/*', /[^*]*\*+([^/*][^*]*\*+)*/, '/')),
      ),

    // ------------------------------------------------------------- annotations
    annotation: ($) =>
      choice(
        prec.right(
          seq(
            field('name', $.annotation_name),
            optional(
              seq('(', commaSep(choice($.string, $.number, $.identifier)), ')'),
            ),
          ),
        ),
        // The `@asm fn` form: `@asm` is also an expression keyword, so it lexes
        // as its own token. Here it takes no `(...)` -- that is the expression.
        field('name', alias('@asm', $.annotation_name)),
      ),
    annotation_name: ($) => token(/@[A-Za-z_]\w*/),

    // ------------------------------------------------------------ declarations
    _declaration: ($) =>
      choice(
        $.struct_declaration,
        $.enum_declaration,
        $.error_declaration,
        $.type_alias,
        $.function_definition,
        $.function_prototype,
        $.global_variable,
        $.const_declaration,
        $.conditional,
        $.directive,
      ),

    // Compile-time diagnostic directives: `@static_assert(cond, "msg");`,
    // `@error("msg");`, `@warning("msg");`. Unlike annotations they stand
    // alone (ending in `;`) and take full constant expressions, so their
    // names lex as dedicated tokens, like `@if`/`@else` do.
    directive: ($) =>
      seq(
        field(
          'name',
          alias(
            choice('@static_assert', '@error', '@warning'),
            $.annotation_name,
          ),
        ),
        '(',
        commaSep1($._expression),
        ')',
        ';',
      ),

    // A `union` shares the struct shape (the compiler rejects `extends` and
    // defaults on it later; a highlighting grammar doesn't enforce that).
    struct_declaration: ($) =>
      seq(
        repeat($.annotation),
        choice('struct', 'union'),
        field('name', alias($.identifier, $.type_identifier)),
        optional($.type_parameters),
        optional(seq('extends', field('base', $._type))),
        choice(';', $.field_list),
      ),

    field_list: ($) =>
      seq(
        '{',
        repeat(
          seq(
            field('name', $.identifier),
            ':',
            $._type,
            optional(seq('=', field('default', $._expression))),
            ';',
          ),
        ),
        '}',
      ),

    enum_declaration: ($) =>
      seq(
        repeat($.annotation),
        'enum',
        field('name', alias($.identifier, $.type_identifier)),
        optional(seq(':', field('underlying', $._type))),
        $.enum_body,
      ),

    enum_body: ($) => seq('{', commaSep($.enum_member), optional(','), '}'),

    enum_member: ($) =>
      seq(field('name', $.identifier), '=', field('value', $._expression)),

    // `error` is a contextual keyword too (an identifier elsewhere -- and
    // `error(...)` in expression position is the result constructor, an
    // ordinary call to this grammar): `error <name> { ... }` declares a
    // nominal error type. A variant auto-numbers, takes an explicit value,
    // or carries a display string -- all `= expression` to the grammar.
    error_declaration: ($) =>
      seq(
        repeat($.annotation),
        'error',
        field('name', alias($.identifier, $.type_identifier)),
        $.error_body,
      ),

    error_body: ($) => seq('{', commaSep($.error_member), optional(','), '}'),

    error_member: ($) =>
      seq(
        field('name', $.identifier),
        optional(seq('=', field('value', $._expression))),
      ),

    // `type` is a contextual keyword (an identifier elsewhere); tree-sitter's
    // keyword extraction via `word` keeps it usable as a plain identifier.
    type_alias: ($) =>
      seq(
        repeat($.annotation),
        'type',
        field('name', alias($.identifier, $.type_identifier)),
        // A generic alias carries a type-parameter list: `type entry<T> = ...`.
        optional($.type_parameters),
        '=',
        field('value', $._type),
        ';',
      ),

    // A parameter may declare a default type: `<T = int64>`. Trailing-only
    // and earlier-parameter references are compiler rules, not grammar rules.
    type_parameters: ($) =>
      seq(
        '<',
        commaSep1(
          seq(
            alias($.identifier, $.type_identifier),
            optional(seq('extends', field('bound', $._type))),
            optional(seq('=', field('default', $._type))),
          ),
        ),
        '>',
      ),

    function_definition: ($) =>
      seq(
        repeat($.annotation),
        'fn',
        field('name', $.identifier),
        optional($.type_parameters),
        $.parameter_list,
        // `-> mut T` marks a mut return (a function returning an lvalue);
        // the fn(...) -> mut T pointer *type* below spells it too.
        optional(seq('->', optional('mut'), field('return_type', $._type))),
        field('body', choice($.block, $.asm_block)),
      ),

    // A bodyless signature ending in `;`: an `@extern` declaration (C ABI) or
    // a plain prototype for a concrete mcc function defined in another object
    // (the form interface stubs emit). Same shape either way; the annotation
    // is what distinguishes them, and a highlighter need not.
    // A generic prototype is the `@removed` tombstone form: it never
    // instantiates, so it is the one generic that may go bodiless.
    function_prototype: ($) =>
      seq(
        repeat($.annotation),
        'fn',
        field('name', $.identifier),
        optional($.type_parameters),
        $.parameter_list,
        // Interface stubs re-emit `-> mut` on prototypes, so it parses here.
        optional(seq('->', optional('mut'), field('return_type', $._type))),
        ';',
      ),

    parameter_list: ($) =>
      seq('(', commaSep(choice($.parameter, $.variadic_parameter)), ')'),

    parameter: ($) =>
      seq(
        // Per-parameter annotations stack (`@noalias @nonnull p: T*`).
        repeat($.annotation),
        optional(choice('const', 'mut')),
        field('name', $.identifier),
        ':',
        $._type,
      ),

    variadic_parameter: ($) => '...',

    global_variable: ($) =>
      seq(
        repeat($.annotation),
        'let',
        field('name', $.identifier),
        optional(seq(':', $._type)),
        optional(seq('=', field('value', $._expression))),
        ';',
      ),

    const_declaration: ($) =>
      seq(
        repeat($.annotation),
        'const',
        field('name', $.identifier),
        optional(seq(':', $._type)),
        '=',
        field('value', $._expression),
        ';',
      ),

    // ---------------------------------------------------- compile-time @if/@else
    conditional: ($) =>
      seq(
        field('name', alias('@if', $.annotation_name)),
        '(',
        field('condition', $._expression),
        ')',
        field('consequence', $._conditional_body),
        optional(
          seq(
            field('else', alias('@else', $.annotation_name)),
            choice($.conditional, field('alternative', $._conditional_body)),
          ),
        ),
      ),

    _conditional_body: ($) => choice($.declaration_block, $.block),
    // A top-level @if branch holds declarations and may also carry conditional
    // imports (resolved per target).
    declaration_block: ($) =>
      seq('{', repeat(choice($._declaration, $.import)), '}'),

    // -------------------------------------------------------------------- types
    _type: ($) =>
      choice(
        $.const_type,
        $.pointer_type,
        $.array_type,
        $.function_type,
        $._type_name,
        $.grouped_type,
      ),

    // `const T` -- the read-only qualifier binding the whole following type
    // (the element of a slice<const T>, a const function-type parameter).
    const_type: ($) => prec.right(seq('const', $._type)),

    _type_name: ($) =>
      prec.right(
        seq(
          optional(choice('struct', 'union')),
          field('name', alias($.identifier, $.type_identifier)),
          optional($.type_arguments),
        ),
      ),

    type_arguments: ($) => seq('<', commaSep1($._type), '>'),

    // A `*` following a cast type is ambiguous: pointer star (`x as int32*`)
    // or multiplication (`x as uint64 * 4`). The compiler settles it by
    // lookahead (a `*` whose next token can begin an expression multiplies);
    // here the GLR parser explores both and the reading that survives wins.
    // When both survive (`x as T * *p`) the negative dynamic precedence makes
    // multiplication win, matching the compiler.
    pointer_type: ($) =>
      prec.dynamic(-1, prec(PREC.as, seq($._type, repeat1('*')))),
    array_type: ($) => prec(1, seq($._type, repeat1($.dimension))),
    // A dimension is any constant expression (e.g. `[N + 1]`); `[]` is an
    // inferred (or flexible-array-member) dimension.
    dimension: ($) => seq('[', optional($._expression), ']'),

    function_type: ($) =>
      prec.right(
        seq(
          'fn',
          '(',
          // A parameter type takes the per-parameter annotation slot
          // (`fn(@nonnull char*) -> int32` carries the @nonnull contract)
          // and a `mut` marker (`fn(mut char)` spells the by-reference
          // convention); `const` rides in through const_type. The return
          // slot takes `mut` too (`fn(uint64) -> mut char` spells a mut
          // return), like the declaration rules above.
          commaSep(
            choice(
              seq(repeat($.annotation), optional('mut'), $._type),
              $.variadic_parameter,
            ),
          ),
          ')',
          optional(seq('->', optional('mut'), $._type)),
        ),
      ),

    grouped_type: ($) => seq('(', $._type, ')'),

    // --------------------------------------------------------------- statements
    block: ($) => seq('{', repeat($._statement), '}'),

    _statement: ($) =>
      choice(
        $.block,
        $.return_statement,
        $.emit_statement,
        $.let_statement,
        $.if_statement,
        $.case_statement,
        $.with_statement,
        $.while_statement,
        $.break_statement,
        $.continue_statement,
        $.unreachable_statement,
        $.defer_statement,
        $.for_statement,
        $.conditional,
        $.assignment_statement,
        $.expression_statement,
      ),

    return_statement: ($) => seq('return', optional($._expression), ';'),
    emit_statement: ($) => seq('emit', $._expression, ';'),

    let_statement: ($) =>
      seq(
        'let',
        field('name', $.identifier),
        optional(seq(':', $._type)),
        optional(seq('=', field('value', $._expression))),
        ';',
      ),

    if_statement: ($) =>
      prec.right(
        seq(
          'if',
          '(',
          field('condition', $._expression),
          ')',
          field('consequence', $._body),
          optional(seq('else', field('alternative', $._body))),
        ),
      ),

    _body: ($) => $._statement,

    // `with (t = v as T) body; else other;` -- the checked-`as` test. The
    // head is initializer-style: binding name first, then the subject-and-
    // pattern `v as T`, which parses as a cast_expression here.
    with_statement: ($) =>
      prec.right(
        seq(
          'with',
          '(',
          field('binding', $.identifier),
          '=',
          field('subject', $._expression),
          ')',
          field('consequence', $._body),
          optional(seq('else', field('alternative', $._body))),
        ),
      ),

    case_statement: ($) =>
      seq('case', '(', field('subject', $._expression), ')', '{', repeat($.when_arm), optional($.else_arm), '}'),

    when_arm: ($) =>
      seq('when', commaSep1($._expression), ':', repeat($._statement)),
    else_arm: ($) => seq('else', ':', repeat($._statement)),

    while_statement: ($) =>
      seq(choice('while', 'until'), '(', field('condition', $._expression), ')', field('body', $._body)),

    break_statement: ($) => seq('break', ';'),
    continue_statement: ($) => seq('continue', ';'),
    unreachable_statement: ($) => seq('unreachable', ';'),
    defer_statement: ($) => seq('defer', $._body),

    for_statement: ($) =>
      seq('for', field('variable', $.identifier), 'in', field('iterable', $._expression), field('body', $._body)),

    assignment_statement: ($) =>
      seq(
        field('target', $._expression),
        field(
          'operator',
          choice('=', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>='),
        ),
        field('value', $._expression),
        ';',
      ),

    expression_statement: ($) => seq($._expression, ';'),

    // -------------------------------------------------------------- expressions
    _expression: ($) =>
      choice(
        $.ternary_expression,
        $.logical_expression,
        $.binary_expression,
        $.cast_expression,
        $.unary_expression,
        $.try_expression,
        $._postfix_expression,
      ),

    // `try f() except (err) { H } [else { S }]` -- the result handler form.
    // `try` binds the call chain that follows (a unary-level prefix, so the
    // whole form composes as an operand) and carries its except clause; the
    // bare propagation form and the `??` fallback are later stages. The
    // binder is parenthesized, both bodies are braced blocks, and the
    // optional `else` is the ok-arm block.
    try_expression: ($) =>
      prec.right(
        PREC.unary,
        seq('try', field('operand', $._expression), $.except_clause),
      ),

    except_clause: ($) =>
      prec.right(
        seq(
          'except',
          '(',
          field('binder', $.identifier),
          ')',
          field('handler', $.block),
          optional(seq('else', field('alternative', $.block))),
        ),
      ),

    // `cond ? a : b`, the loosest operator and right-associative, so
    // `a ? b : c ? d : e` is `a ? b : (c ? d : e)`.
    ternary_expression: ($) =>
      prec.right(
        PREC.ternary,
        seq(
          field('condition', $._expression),
          '?',
          field('consequence', $._expression),
          ':',
          field('alternative', $._expression),
        ),
      ),

    logical_expression: ($) =>
      choice(
        prec.left(PREC.or, seq($._expression, 'or', $._expression)),
        prec.left(PREC.and, seq($._expression, 'and', $._expression)),
      ),

    binary_expression: ($) => {
      const table = [
        [PREC.equality, choice('==', '!=')],
        [PREC.relational, choice('<', '<=', '>', '>=')],
        [PREC.bitor, '|'],
        [PREC.bitxor, '^'],
        [PREC.bitand, '&'],
        [PREC.shift, choice('<<', '>>')],
        [PREC.add, choice('+', '-')],
        [PREC.mul, choice('*', '/', '%')],
      ];
      return choice(
        ...table.map(([p, op]) =>
          prec.left(p, seq($._expression, field('operator', op), $._expression)),
        ),
      );
    },

    // No associativity: at `x as T . *` the cast's precedence ties with
    // pointer_type's and the declared conflict lets the GLR parser fork
    // (see pointer_type). Chained casts (`x as T as U`) stay unambiguous.
    cast_expression: ($) =>
      prec(PREC.as, seq($._expression, 'as', field('type', $._type))),

    unary_expression: ($) =>
      prec.right(PREC.unary, seq(field('operator', choice('-', '!', '*', '&', '~')), $._expression)),

    _postfix_expression: ($) =>
      choice(
        $.index_expression,
        $.slice_expression,
        $.member_expression,
        $.call_expression,
        $.nonnull_assert_expression,
        $._primary_expression,
      ),

    // Postfix `p!`, the non-null assertion. It never collides with `!=`:
    // the lexer folds `!=` into a single comparison token greedily.
    nonnull_assert_expression: ($) =>
      prec(PREC.postfix, seq(field('operand', $._expression), '!')),

    index_expression: ($) =>
      prec(PREC.postfix, seq(field('base', $._expression), '[', field('index', $._expression), ']')),

    // `base[start:end]`, the sub-slice; either bound optional (`s[1:]`,
    // `s[:2]`, `s[:]`). A full expression parses before the slice `:` is
    // considered, so a ternary start consumes its own `:` greedily, matching
    // the compiler (`s[flag ? 1 : 2 : 3]` is start `flag ? 1 : 2`, end `3`).
    // There is no step form: `::` never appears (mcc lexes it as one token,
    // used only by enum access), so a lone `:` is always the slice colon.
    slice_expression: ($) =>
      prec(
        PREC.postfix,
        seq(
          field('base', $._expression),
          '[',
          optional(field('start', $._expression)),
          ':',
          optional(field('end', $._expression)),
          ']',
        ),
      ),

    member_expression: ($) =>
      prec(PREC.postfix, seq(field('base', $._expression), field('operator', choice('.', '->')), field('field', $.identifier))),

    call_expression: ($) =>
      prec(PREC.call, seq(field('function', $._expression), optional($.type_arguments), field('arguments', $.argument_list))),

    argument_list: ($) => seq('(', commaSep($._expression), ')'),

    _primary_expression: ($) =>
      choice(
        $.number,
        $.float,
        $.string,
        $.f_string,
        $.char,
        $.boolean,
        $.null,
        $.enum_access,
        $.struct_literal,
        $.identifier_expression,
        $.parenthesized_expression,
        $.block_expression,
        $.array_expression,
        $.sizeof_expression,
        $.alignof_expression,
        $.offsetof_expression,
        $.typename_expression,
        $.len_expression,
        $.asm_expression,
      ),

    enum_access: ($) =>
      seq(
        field('enum', alias($.identifier, $.type_identifier)),
        '::',
        field('member', $.identifier),
      ),

    // `Name { field = value, ... }` (the `struct`/`union` keyword optional).
    // In real mcc a literal is disabled in statement-head position (the `{`
    // would read as a block); a highlighting grammar leans on GLR instead.
    struct_literal: ($) =>
      prec.dynamic(
        1,
        seq(
          optional(choice('struct', 'union')),
          field('type', alias($.identifier, $.type_identifier)),
          optional($.type_arguments),
          '{',
          commaSep($.field_initializer),
          optional(','),
          '}',
        ),
      ),

    field_initializer: ($) =>
      seq(field('name', $.identifier), '=', field('value', $._expression)),

    identifier_expression: ($) => $.identifier,
    parenthesized_expression: ($) => seq('(', $._expression, ')'),
    block_expression: ($) => prec.dynamic(-1, seq('{', repeat($._statement), '}')),
    array_expression: ($) => seq('[', commaSep($._expression), optional(','), ']'),
    sizeof_expression: ($) => seq('sizeof', '(', $._type, ')'),
    alignof_expression: ($) => seq('alignof', '(', $._type, ')'),
    offsetof_expression: ($) =>
      seq('offsetof', '(', $._type, ',', field('field', $.identifier), ')'),
    typename_expression: ($) => seq('typename', '(', $._type, ')'),
    len_expression: ($) => seq('len', '(', $._expression, ')'),

    // ---------------------------------------------------------------- f-strings
    // An interpolated string literal, `f"..."` (parser.parse_fstring). Unlike
    // the opaque `string` token, its interior is structured: literal content,
    // escapes, `{{`/`}}` literal braces, and `{...}` holes carrying a real
    // expression, so hole expressions highlight natively. A hole is a full
    // expression, then an optional Python-style `=` inspector, then an
    // optional `:modifier` whose text runs raw to the closing brace (the
    // compiler parses the hole's expression first, so a ternary keeps its
    // own `:` and only a colon *after* the expression starts the modifier --
    // the LR automaton reproduces that naturally). f-strings are single-line,
    // like plain strings. One divergence from the compiler: it unescapes the
    // literal before sub-parsing holes, so a nested string literal spelled
    // with escaped quotes (`f"{s == \"x\"}"`) is valid mcc that this grammar
    // reads as an escape followed by an expression error; unescaped nested
    // literals (`f"{c == 'x'}"`) parse fine.
    f_string: ($) =>
      seq(
        'f"',
        repeat(
          choice(
            $.string_content,
            $.escape_sequence,
            $.interpolation,
          ),
        ),
        token.immediate('"'),
      ),

    // The runs between holes. token.immediate (no extras) keeps whitespace
    // inside the literal, and its lexical precedence keeps `//` in string
    // text from lexing as a comment.
    string_content: ($) => token.immediate(prec(1, /[^"\\{}\n]+/)),
    escape_sequence: ($) => token.immediate(choice(/\\./, '{{', '}}')),

    interpolation: ($) =>
      seq(
        '{',
        $._expression,
        optional('='),
        optional(
          seq(
            ':',
            optional(alias(token.immediate(/[^}\n]+/), $.format_spec)),
          ),
        ),
        '}',
      ),

    // ----------------------------------------------------------- inline asm
    asm_expression: ($) =>
      seq(
        alias('@asm', $.annotation_name),
        optional($.clobbers),
        $.argument_list,
        optional(seq('->', $._type)),
        $.asm_block,
      ),

    clobbers: ($) =>
      seq(alias('@clobbers', $.annotation_name), '(', commaSep1($.string), ')'),

    asm_block: ($) => seq('{', repeat1($.string), '}'),

    // ------------------------------------------------------------------ tokens
    identifier: ($) => /[A-Za-z_]\w*/,

    number: ($) => token(choice(/0[xX][0-9a-fA-F]+/, /\d+/)),
    float: ($) => token(choice(/\d+\.\d+([eE][+-]?\d+)?/, /\d+[eE][+-]?\d+/)),
    string: ($) => token(seq('"', repeat(choice(/[^"\\\n]/, /\\./)), '"')),
    char: ($) => token(seq("'", choice(/[^'\\\n]/, /\\./), "'")),
    boolean: ($) => choice('true', 'false'),
    null: ($) => 'null',
  },
});
