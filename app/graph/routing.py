"""Deciding which tool answers a question.

Deliberately a regex, not an LLM classifier: routing every question through a
model would spend quota on each turn to make a decision that two patterns
settle, and it would be non-deterministic to test. The cost is recall — an
unusual phrasing of "summarize" falls through to retrieval, which still
answers, just from relevance-ranked chunks instead of document openings. That
is the safe direction to fail in.

Only *whole-workspace* summary requests route to the tool. "Summarize the
leave policy" names a topic, so retrieval serves it better than the openings
of every document.
"""

import re

from app.synthesis import Route

_SUMMARY_RE = re.compile(
    r"""^\W*
    (?:can\syou\s|could\syou\s|please\s)*
    (?:what(?:'s|\sis|\sare)\s|give\sme\s|provide\s|write\s|make\s)?
    (?:a\s|an\s|the\s)?
    (?:brief\s|short\s|quick\s|high[\s-]level\s)?
    (?:tl;?dr|summar(?:y|ise|ize)|overview|
       key\s(?:points|takeaways)|main\s(?:points|ideas))
    (?:\sof|\sfor|\son|\sabout)?
    (?:\s(?:the\s|these\s|this\s|all\s(?:of\s)?|my\s|our\s)*
       (?:uploaded\s|attached\s|provided\s)?
       (?:documents?|docs?|files?|uploads?|content|material|
          everything|it|them))?
    \W*$""",
    re.IGNORECASE | re.VERBOSE,
)

_ABOUT_RE = re.compile(
    r"""^\W*
    what(?:'s|\sis|\sare)\s
    (?:this|these|the|my|our)\s?
    (?:documents?|docs?|files?|uploads?)?\s*
    (?:about|cover|covering)
    \W*$""",
    re.IGNORECASE | re.VERBOSE,
)


def classify(question: str) -> Route:
    """`"summarize"` for a whole-workspace summary request, else `"qa"`."""
    text = question.strip()
    if _SUMMARY_RE.match(text) or _ABOUT_RE.match(text):
        return "summarize"
    return "qa"
