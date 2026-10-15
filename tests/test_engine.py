"""Tests for the template engine."""

from __future__ import annotations

import unittest

from makemaker.engine import (
    FILTERS,
    Context,
    Template,
    TemplateError,
    cache_info,
    compile_template,
    case_camel,
    case_kebab,
    case_pascal,
    case_snake,
    case_screaming,
    render,
    stable_guid,
    stable_id,
    used_variables,
)


class SubstitutionTests(unittest.TestCase):
    def test_plain_substitution(self):
        self.assertEqual(render("Hello {{ name }}!", {"name": "World"}), "Hello World!")

    def test_none_renders_as_empty(self):
        self.assertEqual(render("[{{ value }}]", {"value": None}), "[]")

    def test_numbers_and_booleans(self):
        self.assertEqual(render("{{ a }}{{ b }}", {"a": 3, "b": True}), "3True")

    def test_literal_braces_are_preserved(self):
        source = 'android { defaultConfig { id = "a.b" } }\nfunc f() { { } }\n${gradle}\n'
        self.assertEqual(render(source, {}), source)

    def test_unterminated_tag_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{{ oops", {})


class ExpressionTests(unittest.TestCase):
    def test_arithmetic_precedence(self):
        self.assertEqual(render("{{ 1 + 2 * 3 }}", {}), "7")
        self.assertEqual(render("{{ (1 + 2) * 3 }}", {}), "9")
        self.assertEqual(render("{{ 7 // 2 }}/{{ 7 % 2 }}", {}), "3/1")

    def test_comparison_and_membership(self):
        self.assertEqual(render("{{ 'a' in items }}", {"items": ["a"]}), "True")
        self.assertEqual(render("{{ 'z' not in items }}", {"items": ["a"]}), "True")
        self.assertEqual(render("{{ 1 < 2 }}", {}), "True")

    def test_boolean_operators_short_circuit(self):
        self.assertEqual(render("{{ missing or 'fallback' }}", {}), "fallback")
        self.assertEqual(render("{{ 'a' and 'b' }}", {}), "b")

    def test_dotted_lookup(self):
        self.assertEqual(render("{{ a.b.c }}", {"a": {"b": {"c": "deep"}}}), "deep")

    def test_undefined_is_an_error_when_printed(self):
        with self.assertRaises(TemplateError) as caught:
            render("{{ nope }}", {})
        self.assertIn("undefined variable 'nope'", str(caught.exception))

    def test_undefined_is_an_error_when_compared(self):
        with self.assertRaises(TemplateError):
            render("{{ nope == 1 }}", {})

    def test_undefined_is_tolerated_by_truthiness(self):
        self.assertEqual(render("{% if not nope %}ok{% endif %}", {}), "ok")

    def test_error_reports_line_and_column(self):
        with self.assertRaises(TemplateError) as caught:
            render("line one\n{{ nope }}\n", {})
        self.assertIn("line 2", str(caught.exception))


class FilterTests(unittest.TestCase):
    def test_case_filters(self):
        values = {"name": "My Cool App"}
        self.assertEqual(render("{{ name | pascal }}", values), "MyCoolApp")
        self.assertEqual(render("{{ name | camel }}", values), "myCoolApp")
        self.assertEqual(render("{{ name | snake }}", values), "my_cool_app")
        self.assertEqual(render("{{ name | kebab }}", values), "my-cool-app")
        self.assertEqual(render("{{ name | screaming }}", values), "MY_COOL_APP")
        self.assertEqual(render("{{ name | identifier }}", values), "my_cool_app")

    def test_filters_with_arguments(self):
        self.assertEqual(render("{{ v | replace('-', '_') }}", {"v": "a-b"}), "a_b")
        self.assertEqual(render("{{ v | trim_end('.tmpl') }}", {"v": "a.c.tmpl"}), "a.c")

    def test_default_filter_accepts_undefined(self):
        self.assertEqual(render("{{ nope | default('x') }}", {}), "x")

    def test_filter_chain(self):
        self.assertEqual(render("{{ v | upper | replace('A', 'Z') }}", {"v": "abc"}), "ZBC")

    def test_filters_apply_to_literals(self):
        self.assertEqual(render("{{ 'not a tag' | quote }}", {}), "'not a tag'")

    def test_unknown_filter_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{{ v | nosuchfilter }}", {"v": 1})

    def test_all_filters_are_callable(self):
        for name, function in FILTERS.items():
            self.assertTrue(callable(function), name)


class BlockTests(unittest.TestCase):
    def test_if_elif_else(self):
        source = "{% if l == 'cpp' %}C++{% elif l == 'c' %}C{% else %}?{% endif %}"
        self.assertEqual(render(source, {"l": "c"}), "C")
        self.assertEqual(render(source, {"l": "cpp"}), "C++")
        self.assertEqual(render(source, {"l": "x"}), "?")

    def test_for_loop_with_loop_variable(self):
        source = "{% for s in srcs %}{{ loop.index }}:{{ s }}\n{% endfor %}"
        self.assertEqual(render(source, {"srcs": ["a.c", "b.c"]}), "1:a.c\n2:b.c\n")

    def test_loop_flags(self):
        source = "{% for s in srcs %}{% if loop.first %}F{% endif %}{{ s }}{% if loop.last %}L{% endif %} {% endfor %}"
        self.assertEqual(render(source, {"srcs": ["a", "b"]}), "Fa bL ")

    def test_empty_loop_renders_nothing(self):
        self.assertEqual(render("{% for s in srcs %}x{% endfor %}", {"srcs": []}), "")

    def test_nested_blocks(self):
        source = (
            "{% for g in groups %}{% for i in g %}{{ i }}{% endfor %};{% endfor %}"
        )
        self.assertEqual(render(source, {"groups": [[1, 2], [3]]}), "12;3;")

    def test_inline_if_keeps_surrounding_text(self):
        self.assertEqual(render("x = {% if a %}1{% else %}2{% endif %};", {"a": False}), "x = 2;")

    def test_block_tags_do_not_leave_blank_lines(self):
        source = "int main() {\n{% if gui %}\n    gui_init();\n{% endif %}\n    return 0;\n}\n"
        self.assertEqual(
            render(source, {"gui": True}),
            "int main() {\n    gui_init();\n    return 0;\n}\n",
        )
        self.assertEqual(render(source, {"gui": False}), "int main() {\n    return 0;\n}\n")

    def test_comments_are_removed(self):
        self.assertEqual(render("a{# note #}b", {}), "ab")

    def test_unterminated_block_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{% if a %}x", {"a": True})
        with self.assertRaises(TemplateError):
            render("{% for x in y %}x", {"y": [1]})

    def test_unknown_block_tag_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{% bogus %}", {})

    def test_stray_endif_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{% if a %}x{% endif %}{% endif %}", {"a": True})


class SetTagTests(unittest.TestCase):
    def test_set_binds_a_value(self):
        self.assertEqual(render("{% set x = 1 %}{{ x }}", {}), "1")

    def test_set_can_use_filters(self):
        self.assertEqual(render("{% set p = name | identifier %}{{ p }}", {"name": "My App"}), "my_app")

    def test_set_is_repeatable_and_stable(self):
        output = render("{% set i = 'key' | id %}{{ i }}/{{ i }}", {})
        first, second = output.split("/")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 24)

    def test_set_leaves_no_blank_line(self):
        self.assertEqual(render("A\n{% set z = 1 %}\nB\n", {}), "A\nB\n")

    def test_malformed_set_is_an_error(self):
        for source in ("{% set %}", "{% set x %}", "{% set = 1 %}"):
            with self.assertRaises(TemplateError, msg=source):
                render(source, {})


class HelperTests(unittest.TestCase):
    def test_case_helpers_split_camel_case(self):
        self.assertEqual(case_pascal("helloWorld"), "HelloWorld")
        self.assertEqual(case_camel("HelloWorld"), "helloWorld")
        self.assertEqual(case_snake("HelloWorld"), "hello_world")
        self.assertEqual(case_kebab("HelloWorld"), "hello-world")
        self.assertEqual(case_screaming("hello world"), "HELLO_WORLD")

    def test_stable_ids_are_deterministic_and_sized(self):
        self.assertEqual(stable_id("a"), stable_id("a"))
        self.assertNotEqual(stable_id("a"), stable_id("b"))
        self.assertEqual(len(stable_id("a")), 24)
        self.assertEqual(len(stable_guid("a")), 36)

    def test_used_variables(self):
        source = "{% if lang == 'c' %}{{ slug }}{% endif %}{{ other }}"
        self.assertEqual(used_variables(source), ["lang", "other", "slug"])

    def test_context_scopes(self):
        context = Context({"a": 1})
        context.push({"a": 2})
        self.assertEqual(context.get(["a"]), 2)
        context.pop()
        self.assertEqual(context.get(["a"]), 1)
        with self.assertRaises(KeyError):
            context.get(["missing"])

    def test_template_object_is_reusable(self):
        template = Template("{{ v }}")
        self.assertEqual(template.render({"v": 1}), "1")
        self.assertEqual(template.render({"v": 2}), "2")


class RawBlockTest(unittest.TestCase):
    """``{% raw %}`` must pass text through with no interpolation."""

    def test_raw_passes_tags_through_untouched(self):
        out = render("{% raw %}{{ not_a_variable }}{% endraw %}", {})
        self.assertEqual(out, "{{ not_a_variable }}")

    def test_raw_surrounds_rendered_text(self):
        out = render("{{ v }}|{% raw %}{{ v }}{% endraw %}", {"v": "A"})
        self.assertEqual(out, "A|{{ v }}")

    def test_raw_keeps_cmake_dollar_brace_literals(self):
        # CMake generator expressions collide with our tag syntax.
        source = "target_compile_features(${{ '{' }}raw{{ '}' }})"
        self.assertIn("{", source)
        out = render("{% raw %}set(X $<CONFIG:Debug>){% endraw %}", {})
        self.assertEqual(out, "set(X $<CONFIG:Debug>)")

    def test_raw_handles_newlines_like_other_block_tags(self):
        # A block tag on its own line consumes its trailing newline, exactly
        # as {% if %} does. Assert the two agree rather than a made-up rule.
        via_if = render("a\n{% if t %}b\n{% endif %}\nc", {"t": True})
        via_raw = render("a\n{% raw %}b\n{% endraw %}\nc", {})
        self.assertEqual(via_if, "a\nb\nc")
        self.assertEqual(via_raw, via_if)

    def test_raw_without_endraw_is_an_error(self):
        with self.assertRaises(TemplateError) as ctx:
            render("{% raw %}never closed", {})
        self.assertIn("unterminated", str(ctx.exception).lower())

    def test_stray_endraw_is_rejected(self):
        with self.assertRaises(TemplateError):
            render("{% endraw %}", {})

    def test_raw_content_is_not_scanned_for_other_tags(self):
        out = render("{% raw %}{% if x %}{# c #}{% endraw %}", {})
        self.assertEqual(out, "{% if x %}{# c #}")


class JoinFilterTest(unittest.TestCase):
    def test_join_strings(self):
        self.assertEqual(render("{{ v | join(\"-\") }}", {"v": ["a", "b"]}), "a-b")

    def test_join_coerces_non_strings(self):
        self.assertEqual(render("{{ v | join(\"\") }}", {"v": [1, 2, 3]}), "123")

    def test_join_defaults_to_empty_separator(self):
        self.assertEqual(render("{{ v | join() }}", {"v": ["a", "b"]}), "ab")

    def test_join_empty_list(self):
        self.assertEqual(render("{{ v | join(\"x\") }}", {"v": []}), "")

    def test_join_on_non_iterable_fails(self):
        with self.assertRaises(TemplateError):
            render("{{ v | join(\"-\") }}", {"v": 5})

    def test_join_is_registered(self):
        self.assertIn("join", FILTERS)


class CacheTest(unittest.TestCase):
    """The compile cache is bounded so long-lived processes do not grow."""

    def setUp(self):
        from makemaker import engine

        self._engine = engine
        self._limit = engine._CACHE_LIMIT
        engine._CACHE.clear()

    def tearDown(self):
        self._engine._CACHE.clear()
        self._engine._CACHE_LIMIT = self._limit

    def test_cache_memoises_compiled_templates(self):
        first = compile_template("{{ v }}", name="same.tmpl", cache=True)
        second = compile_template("{{ v }}", name="same.tmpl", cache=True)
        self.assertIs(first, second)

    def test_cache_info_reports_size_and_limit(self):
        self.assertEqual(cache_info(), (0, self._limit))
        compile_template("{{ v }}", name="a.tmpl", cache=True)
        entries, limit = cache_info()
        self.assertEqual(entries, 1)
        self.assertEqual(limit, self._limit)

    def test_cache_stops_at_its_limit_and_still_works(self):
        for index in range(self._limit + 50):
            compile_template("{{ v }}", name=f"t{index}.tmpl", cache=True)
        entries, limit = cache_info()
        self.assertEqual(entries, limit, "cache grew past its limit")
        # Evicting the oldest entries must not break the newest one.
        newest = compile_template(
            "{{ v }}", name=f"t{self._limit + 49}.tmpl", cache=True
        )
        self.assertEqual(newest.render({"v": "ok"}), "ok")

    def test_oldest_entry_is_evicted_first(self):
        source = "{{ v }}"
        for index in range(self._limit):
            compile_template(source, name=f"e{index}.tmpl", cache=True)
        compile_template(source, name="fresh.tmpl", cache=True)
        # The cache is keyed by (name, source) so that editing a template
        # file can never return a stale compilation.
        names = [name for name, _ in self._engine._CACHE]
        self.assertNotIn("e0.tmpl", names, "oldest entry was not evicted")
        self.assertIn("e1.tmpl", names, "evicted too much")
        self.assertIn("fresh.tmpl", names)
        self.assertEqual(len(names), self._limit)

    def test_same_name_different_source_is_a_separate_entry(self):
        first = compile_template("{{ v }}", name="x.tmpl", cache=True)
        second = compile_template("{{ v }}!", name="x.tmpl", cache=True)
        self.assertIsNot(first, second)
        self.assertEqual(second.render({"v": 1}), "1!")
        # Editing a template must not hand back the previous compilation.
        self.assertEqual(first.render({"v": 1}), "1")

    def test_render_bypasses_the_cache(self):
        render("{{ v }}", {"v": 1})
        self.assertEqual(cache_info()[0], 0)


if __name__ == "__main__":
    unittest.main()
