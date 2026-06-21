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
        $.function_definition,
        $.extern_function,
        $.global_variable,
        $.const_declaration,
        $.conditional,
      ),

    struct_declaration: ($) =>
      seq(
        repeat($.annotation),
        'struct',
        field('name', alias($.identifier, $.type_identifier)),
        optional($.type_parameters),
        optional(seq('extends', field('base', $._type))),
        choice(';', $.field_list),
      ),

    field_list: ($) =>
      seq('{', repeat(seq(field('name', $.identifier), ':', $._type, ';')), '}'),

    type_parameters: ($) => seq('<', commaSep1(alias($.identifier, $.type_identifier)), '>'),

    function_definition: ($) =>
      seq(
        repeat($.annotation),
        'fn',
        field('name', $.identifier),
        optional($.type_parameters),
        $.parameter_list,
        optional(seq('->', field('return_type', $._type))),
        field('body', choice($.block, $.asm_block)),
      ),

    extern_function: ($) =>
      seq(
        repeat($.annotation),
        'fn',
        field('name', $.identifier),
        $.parameter_list,
        optional(seq('->', field('return_type', $._type))),
        ';',
      ),

    parameter_list: ($) =>
      seq('(', commaSep(choice($.parameter, $.variadic_parameter)), ')'),

    parameter: ($) =>
      seq(optional('const'), field('name', $.identifier), ':', $._type),

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
    declaration_block: ($) => seq('{', repeat($._declaration), '}'),

    // -------------------------------------------------------------------- types
    _type: ($) =>
      choice($.pointer_type, $.array_type, $.function_type, $._type_name, $.grouped_type),

    _type_name: ($) =>
      prec.right(
        seq(
          optional('struct'),
          field('name', alias($.identifier, $.type_identifier)),
          optional($.type_arguments),
        ),
      ),

    type_arguments: ($) => seq('<', commaSep1($._type), '>'),

    // A `*` following a cast type is taken as a pointer star (the common
    // `x as int32*`), winning over multiplication. The rare `x as T * y` (cast
    // then multiply without parens) is the cost; parenthesize it. Settling this
    // perfectly needs lexer lookahead (an external scanner), which a
    // highlighting grammar does without.
    pointer_type: ($) => prec(PREC.unary, seq($._type, repeat1('*'))),
    array_type: ($) => prec(1, seq($._type, repeat1($.dimension))),
    dimension: ($) => seq('[', optional(choice($.number, $.identifier)), ']'),

    function_type: ($) =>
      prec.right(seq('fn', '(', commaSep($._type), ')', optional(seq('->', $._type)))),

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
        $.while_statement,
        $.break_statement,
        $.continue_statement,
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

    case_statement: ($) =>
      seq('case', '(', field('subject', $._expression), ')', '{', repeat($.when_arm), optional($.else_arm), '}'),

    when_arm: ($) =>
      seq('when', commaSep1($._expression), ':', repeat($._statement)),
    else_arm: ($) => seq('else', ':', repeat($._statement)),

    while_statement: ($) =>
      seq(choice('while', 'until'), '(', field('condition', $._expression), ')', field('body', $._body)),

    break_statement: ($) => seq('break', ';'),
    continue_statement: ($) => seq('continue', ';'),
    defer_statement: ($) => seq('defer', $._body),

    for_statement: ($) =>
      seq('for', field('variable', $.identifier), 'in', field('iterable', $._expression), field('body', $._body)),

    assignment_statement: ($) =>
      seq(field('target', $._expression), '=', field('value', $._expression), ';'),

    expression_statement: ($) => seq($._expression, ';'),

    // -------------------------------------------------------------- expressions
    _expression: ($) =>
      choice(
        $.ternary_expression,
        $.logical_expression,
        $.binary_expression,
        $.cast_expression,
        $.unary_expression,
        $._postfix_expression,
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

    cast_expression: ($) =>
      prec.left(PREC.as, seq($._expression, 'as', field('type', $._type))),

    unary_expression: ($) =>
      prec.right(PREC.unary, seq(field('operator', choice('-', '!', '*', '&', '~')), $._expression)),

    _postfix_expression: ($) =>
      choice(
        $.index_expression,
        $.member_expression,
        $.call_expression,
        $._primary_expression,
      ),

    index_expression: ($) =>
      prec(PREC.postfix, seq(field('base', $._expression), '[', field('index', $._expression), ']')),

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
        $.char,
        $.boolean,
        $.null,
        $.identifier_expression,
        $.parenthesized_expression,
        $.block_expression,
        $.array_expression,
        $.sizeof_expression,
        $.len_expression,
        $.asm_expression,
      ),

    identifier_expression: ($) => $.identifier,
    parenthesized_expression: ($) => seq('(', $._expression, ')'),
    block_expression: ($) => prec.dynamic(-1, seq('{', repeat($._statement), '}')),
    array_expression: ($) => seq('[', commaSep($._expression), optional(','), ']'),
    sizeof_expression: ($) => seq('sizeof', '(', $._type, ')'),
    len_expression: ($) => seq('len', '(', $._expression, ')'),

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
