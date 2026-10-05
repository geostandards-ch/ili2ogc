"""Facade: an .ili file path (or raw source text) -> an ANTLR tree.

Runs the Lexer+Parser.
"""

import re
from pathlib import Path

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from interlis.antlr.InterlisLexer import InterlisLexer
from interlis.antlr.InterlisParser import InterlisParser

_META_ATTRIBUTE_PREFIX = "!!@"
# eCH-0117 SS4.2 Escape = '\' ('"' | '\' | 'u' HexDigit HexDigit HexDigit HexDigit)
_ESCAPE_RE = re.compile(r'\\(["\\]|u[0-9a-fA-F]{4})')


class SyntaxErrorCollector(ErrorListener):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
        self.errors.append(f"line {line}:{column} {msg}")


# Keywords INTERLIS 2.4 added: plain names in a 2.3 model (ili2c's 2.3 lexer does not reserve them).
_KEYWORDS_SINCE_24 = frozenset(
    getattr(InterlisLexer, name)
    for name in (
        "CHARSET",
        "CONTEXT",
        "DATE",
        "DATETIME",
        "DEFERRED",
        "GENERIC",
        "GENERICS",
        "MULTIAREA",
        "MULTICOORD",
        "MULTIPOLYLINE",
        "MULTISURFACE",
        "NOINCREMENTALTRANSFER",
        "REFSYS",
        "TIMEOFDAY",
        "XMLNS",
    )
)
# Tokens an operand ends with: a sign right after one is a binary operator (`a-1` is `a - 1`), not part of a number.
_OPERAND_END = frozenset(
    getattr(InterlisLexer, name)
    for name in ("Name", "PosNumber", "Number", "Dec", "Float", "STRING", "RPAR", "RSBR", "THIS", "PI", "LNBASE")
)
_SIGNED = frozenset((InterlisLexer.Number, InterlisLexer.Dec, InterlisLexer.Float))
_VERSION_RE = re.compile(r"^\s*(?:!![^\n]*\n\s*|/\*.*?\*/\s*)*INTERLIS\s+(\d\.\d)", re.S)


class _TokenSource:
    """The lexer's tokens with the two context-dependent rules a plain lexer cannot apply.

    A 2.3 model may use a 2.4-only keyword as a name; and the lexer reads `-1` as one signed number even where
    the `-` is a subtraction (`a-1`, eCH-0031 3.13 Term0 = Term1 { ( '+' | '-' ) Term1 }).
    """

    def __init__(self, lexer: InterlisLexer, version: str | None):
        self._lexer = lexer
        self._pending: list = []
        self._previous: int | None = None
        self._as_names = _KEYWORDS_SINCE_24 if version == "2.3" else frozenset()

    def __getattr__(self, name: str):
        return getattr(self._lexer, name)

    def nextToken(self):
        token = self._pending.pop(0) if self._pending else self._lexer.nextToken()
        if token.type in self._as_names:
            token.type = InterlisLexer.Name
        if token.type in _SIGNED and token.text[:1] in "+-" and self._previous in _OPERAND_END:
            sign, number = token.clone(), token.clone()
            sign.type = InterlisLexer.PLUS if token.text[0] == "+" else InterlisLexer.MINUS
            sign.text, sign.stop = token.text[0], token.start
            number.text, number.start, number.column = token.text[1:], token.start + 1, token.column + 1
            if number.type == InterlisLexer.Number:
                number.type = InterlisLexer.PosNumber
            self._pending.insert(0, number)
            token = sign
        if token.channel == 0:
            self._previous = token.type
        return token


def _parse_stream(stream):
    lexer = InterlisLexer(stream)
    version = _VERSION_RE.match(str(stream))
    tokens = CommonTokenStream(_TokenSource(lexer, version.group(1) if version else None))
    parser = InterlisParser(tokens)

    collector = SyntaxErrorCollector()
    parser.removeErrorListeners()
    parser.addErrorListener(collector)

    tree = parser.interlis2def()
    return tree, collector.errors


def parse_file(path: Path):
    """Parse an .ili file, return (interlis2def tree, syntax error list).

    Does NOT call the ModelBuilder - keeps reading and building separate.

    Falls back to ISO-8859-1 if the file isn't valid UTF-8 - some older
    files in the wild (e.g. "obsolete/*_o0.ili" variants) are ISO-8859
    text, not UTF-8. The reference manual (eCH-0031 V2.1.0 §3.5.1, CHARSET
    clause) only fixes the character set allowed in transferred VALUES,
    never the on-disk encoding of the .ili source file itself, so falling
    back to latin-1 breaks no rule. latin-1 can never raise
    UnicodeDecodeError (a 1 byte <-> 1 character mapping over all 256
    values), so no further fallback is needed.
    """
    return _parse_stream(InputStream(read_ili_text(path)))


def read_ili_text(path: Path) -> str:
    """An .ili file's text: UTF-8, else ISO-8859-1 (see `parse_file`)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="iso-8859-1")


def parse_text(text: str):
    """Do the same as `parse_file`, but from in-memory source text.

    Used for the predefined INTERLIS model (see ModelRepository), which
    exists as an .ili file nowhere on disk.
    """
    return _parse_stream(InputStream(text))


def _unquote_interlis_string(value: str) -> str:
    r"""Strip an INTERLIS `STRING` token's quoting/escapes, if present (eCH-0117 SS4.1, same rule as the lexer STRING).

    A bare (unquoted) value is returned as-is; a `"..."` value has its
    `\"`/`\\`/`\uXXXX` escapes resolved - shared by meta-attribute values
    here and by `Expression` string literals (`convert/constraint_eval.py`).
    """
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return value
    inner = value[1:-1]
    return _ESCAPE_RE.sub(
        lambda m: m.group(1) if m.group(1) in ('"', "\\") else chr(int(m.group(1)[1:], 16)),
        inner,
    )


def meta_attribute_comments(text: str) -> list[tuple[int, str, str]]:
    r"""Return every eCH-0117 `!!@Name=Value` meta-attribute comment in `text`.

    eCH-0117 ("Meta-attributs pour modeles INTERLIS") formalizes `!!@...`
    as an INTERLIS line comment (`SingleLineComment : '!!' ~[\r\n]* ->
    channel(HIDDEN);`, vendor/interlis-antlr4/InterlisLexer.g4) whose 3rd
    character is `@` - the grammar itself never sees these (they're on
    ANTLR's hidden channel, invisible to the 121 mapped parser rules), but
    the lexer does NOT discard them (`channel(HIDDEN)`, not `-> skip`), so
    a second, independent lex-only pass over the same source recovers them
    fully, with exact line numbers - no grammar/parser change needed.

    Returns `(line, name, value)` triples in source order (one per
    `Name=Value` pair - a single comment can carry several, separated by
    `;`: `!!@a=1;b=2`). An ordinary `!!` comment (no `@`) is not a
    meta-attribute per eCH-0117 SS3/SS4 and is excluded. Positioning
    (which built instance a given triple actually belongs to - "the first
    following language construct", eCH-0117 SS3) is NOT decided here -
    see InterlisModelBuilder._attach_pending_meta_attributes.
    """
    stream = InputStream(text)
    lexer = InterlisLexer(stream)
    tokens = CommonTokenStream(lexer)
    tokens.fill()
    results: list[tuple[int, str, str]] = []
    for token in tokens.tokens:
        if token.channel == 0 or token.text is None or not token.text.startswith(_META_ATTRIBUTE_PREFIX):
            continue
        body = token.text[len(_META_ATTRIBUTE_PREFIX) :]
        for pair in body.split(";"):
            if "=" not in pair:
                continue
            name, _, raw_value = pair.partition("=")
            name = name.strip()
            if not name:
                continue
            results.append((token.line, name, _unquote_interlis_string(raw_value.strip())))
    return results


def meta_attribute_comments_in_file(path: Path) -> list[tuple[int, str, str]]:
    """Same as `meta_attribute_comments`, reading `path` (same encoding fallback as `parse_file`)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="iso-8859-1")
    return meta_attribute_comments(text)
