; Text objects in nvim-treesitter-textobjects' format (af/if for functions,
; aa/ia for parameters, once mapped in that plugin's config).

(function_definition body: (_) @function.inner) @function.outer
(function_prototype) @function.outer

(parameter) @parameter.inner @parameter.outer
(variadic_parameter) @parameter.inner @parameter.outer

(comment) @comment.outer
