#!/usr/bin/env python3
r"""A LaTeXML formula's text, once.

arXiv's HTML (and ar5iv's) writes every formula as presentation MathML, which is
what a reader sees, and then attaches the same formula again for machines inside
`<semantics>`: its TeX in `<annotation encoding="application/x-tex">`, and in
some renders content MathML in `<annotation-xml>`. A reader of that HTML that
only strips tags keeps every copy, so `5×5` came out `5×55\times 5`, and a
caption copied onto a Notion page carried each formula twice.

`drop_math_annotations` removes the machine copies and keeps the rendered form,
so the tag-stripping that follows yields what a reader sees: captions, section
titles and reference entries, all plain text on the page, read it that way.

`formulas_as_tex` keeps the TeX instead, once, for the reader that compares the
source with what the page STORES: a page keeps a formula as TeX (an equation
block's expression, an inline equation's text), so the audit's source text reads
it as TeX too, or the two sides measure different things.

It depends on nothing but `re`, so the auditor and the injectors that import it
never fail to load because of it.
"""
import re

# `annotation-xml` is tried first and `annotation` must be followed by a space or
# the end of its tag: `<annotation\b` alone also opens `<annotation-xml`, and
# would then run to the NEXT formula's `</annotation>`, taking the words between.
_ANNOTATION = re.compile(
    r"<(annotation-xml|annotation)(?=[\s>/])[^>]*>.*?</\1\s*>", re.DOTALL | re.I)


def drop_math_annotations(markup: str) -> str:
    """`markup` with each formula's TeX and content-MathML copies removed."""
    return _ANNOTATION.sub("", markup or "")


# A LaTeXML formula with its TeX in `alttext`. Formulas do not nest, so the first
# `</math>` closes the one that opened.
_MATH_WITH_TEX = re.compile(
    r'<math(?=[\s>/])[^>]*?\balttext="([^"]*)"[^>]*>.*?</math\s*>', re.DOTALL | re.I)


def formulas_as_tex(markup: str) -> str:
    """`markup` with each formula that carries its TeX (`alttext`) replaced by it.

    The TeX stays entity-encoded, as text in markup is: the caller's one
    unescape decodes it, and decoding here would let a `<` in it read as a tag.
    A formula without `alttext` is left for `drop_math_annotations`.
    """
    return _MATH_WITH_TEX.sub(lambda m: m.group(1), markup or "")
