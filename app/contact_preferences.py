"""Recognize direct sales opt-outs without treating quoted examples as commands."""

import re


def is_direct_opt_out(text: str) -> bool:
    clauses = re.split(r"[.!;\n]+", text.casefold().replace("’", "'"))
    patterns = (
        r"(?:please\s+)?(?:do not|don't|never|stop) (?:call(?:ing)?|contact(?:ing)?) me(?: (?:again|anymore|any more))?(?: please)?",
        r"(?:please\s+)?(?:remove|take) me (?:off|from) (?:your|the) (?:call(?:ing)? |contact )?list",
        r"(?:por favor[, ]+)?no (?:me (?:llames|llamen|vuelvas a llamar|vuelva a llamar)|vuelvas a llamarme|vuelva a llamarme)(?: (?:más|mas|otra vez))?(?:[, ]+por favor)?",
        r"(?:пожалуйста[, ]+)?(?:больше )?не (?:звоните|звони) мне(?: больше)?(?:[, ]+пожалуйста)?",
        r"(?:будь ласка[, ]+)?(?:більше )?не (?:дзвоніть|телефонуйте|дзвони) мені(?: більше)?(?:[, ]+будь ласка)?",
    )
    return any(re.fullmatch(pattern, clause.strip()) for clause in clauses for pattern in patterns)
