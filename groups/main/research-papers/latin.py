#!/usr/bin/env python3
"""Greek letters and typeset digits spelled the way a keyboard spells them.

Shared by paper attribution (`auto_save_qa`) and the Q&A backstop's question
match (`backstop_timing`), which cannot import each other.

Imports nothing but `re`.
"""
import re


# A model named with a Greek letter is written one way in the title and another
# by the person asking about it: the page says "\u03c00", the question says "pi0".
# Nobody types \u03c0 on a phone. Transliterating both sides makes the model name a
# normal token again — and it is the most distinctive token a title has.
_GREEK = {
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma", "\u03b4": "delta",
    "\u03b5": "epsilon", "\u03b6": "zeta", "\u03b7": "eta", "\u03b8": "theta",
    "\u03b9": "iota", "\u03ba": "kappa", "\u03bb": "lambda", "\u03bc": "mu",
    "\u03bd": "nu", "\u03be": "xi", "\u03c0": "pi", "\u03c1": "rho",
    "\u03c3": "sigma", "\u03c4": "tau", "\u03c6": "phi", "\u03c7": "chi",
    "\u03c8": "psi", "\u03c9": "omega",
    "\u0391": "Alpha", "\u0392": "Beta", "\u0393": "Gamma", "\u0394": "Delta",
    "\u039b": "Lambda", "\u03a0": "Pi", "\u03a3": "Sigma", "\u03a6": "Phi",
    "\u03a8": "Psi", "\u03a9": "Omega",
}


# A model name is also typeset with SUBSCRIPT digits — "\u03c0\u2080.\u2087" — which no
# keyboard produces either, and a star between the letter and the number
# ("\u03c0*0.6") splits the token in two. Both are decoration on the same name.
_SUBDIGIT = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"
                          "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079",
                          "01234567890123456789")


def latinize(text: str) -> str:
    """Greek letters spelled out, so a title and a phone keyboard can meet."""
    if not text:
        return ""
    for g, name in _GREEK.items():
        if g in text:
            text = text.replace(g, name)
    text = text.translate(_SUBDIGIT)
    return re.sub(r"(?<=[A-Za-z])\*(?=[0-9])", "", text)
