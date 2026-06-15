from __future__ import annotations

from dataclasses import dataclass

# Token kinds: KEYWORD, IDENT, INT, STRING, OP, PUNCT, STAR, EOF, ERROR.
# Keywords are matched case-insensitively and stored upper-cased; identifiers
# keep their original case. Position is the 0-based offset of the token's first
# character, used for error reporting.

KEYWORDS = frozenset({
    "CREATE", "TABLE", "INSERT", "INTO", "VALUES", "SELECT", "FROM",
    "WHERE", "ORDER", "BY", "ASC", "DESC", "LIMIT", "AND", "OR", "NULL",
    "JOIN", "ON", "GROUP",
})

# Multi-char operators must be tried before their single-char prefixes.
_OPERATORS = ("!=", "<=", ">=", "=", "<", ">")
_PUNCT = frozenset("(),.;")


@dataclass(slots=True, frozen=True)
class Token:
    kind: str
    value: str
    pos: int


def tokenize(sql: str) -> list[Token]:
    """Split SQL text into tokens. Never raises: an unrecognized character
    becomes an ERROR token so the parser can report position and expectation."""
    tokens: list[Token] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif c == "'":
            tok, i = _read_string(sql, i)
            tokens.append(tok)
        elif c.isdigit() or (c == "-" and i + 1 < n and sql[i + 1].isdigit()):
            tok, i = _read_number(sql, i)
            tokens.append(tok)
        elif c.isalpha() or c == "_":
            tok, i = _read_word(sql, i)
            tokens.append(tok)
        elif c == "*":
            tokens.append(Token("STAR", "*", i))
            i += 1
        elif c in _PUNCT:
            tokens.append(Token("PUNCT", c, i))
            i += 1
        else:
            op = next((o for o in _OPERATORS if sql.startswith(o, i)), None)
            if op:
                tokens.append(Token("OP", op, i))
                i += len(op)
            else:
                tokens.append(Token("ERROR", c, i))
                i += 1
    tokens.append(Token("EOF", "", n))
    return tokens


def _read_string(sql: str, i: int) -> tuple[Token, int]:
    start = i
    i += 1  # opening quote
    n = len(sql)
    while i < n and sql[i] != "'":
        i += 1
    if i >= n:
        # Unterminated: report the opening quote's position.
        return Token("ERROR", sql[start:], start), n
    return Token("STRING", sql[start + 1:i], start), i + 1


def _read_number(sql: str, i: int) -> tuple[Token, int]:
    start = i
    i += 1  # first digit or leading '-'
    n = len(sql)
    while i < n and sql[i].isdigit():
        i += 1
    return Token("INT", sql[start:i], start), i


def _read_word(sql: str, i: int) -> tuple[Token, int]:
    start = i
    n = len(sql)
    while i < n and (sql[i].isalnum() or sql[i] == "_"):
        i += 1
    word = sql[start:i]
    upper = word.upper()
    if upper in KEYWORDS:
        return Token("KEYWORD", upper, start), i
    return Token("IDENT", word, start), i


if __name__ == "__main__":
    def kinds(s):
        return [(t.kind, t.value) for t in tokenize(s)]
    assert kinds("SELECT * FROM t") == [
        ("KEYWORD", "SELECT"), ("STAR", "*"), ("KEYWORD", "FROM"),
        ("IDENT", "t"), ("EOF", ""),
    ]
    assert kinds("age >= -18") == [
        ("IDENT", "age"), ("OP", ">="), ("INT", "-18"), ("EOF", ""),
    ]
    assert kinds("'Alice'")[0] == ("STRING", "Alice")
    assert kinds("users.id") == [
        ("IDENT", "users"), ("PUNCT", "."), ("IDENT", "id"), ("EOF", ""),
    ]
    # Never raises; bad char surfaces as ERROR.
    assert tokenize("@")[0].kind == "ERROR"
    assert tokenize("'unterminated")[0].kind == "ERROR"
    print("ok")
