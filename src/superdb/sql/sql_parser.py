from __future__ import annotations

from superdb.errors import ParseError
from superdb.sql.sql_ast import (
    BoolOp,
    ColumnDef,
    ColumnRef,
    Comparison,
    CreateTable,
    FuncCall,
    Insert,
    Join,
    Literal,
    OrderBy,
    Select,
    Statement,
)
from superdb.sql.sql_lexer import Token, tokenize

# Hand-written recursive-descent parser for the supported SQL subset (HW Stage 3).
# Storage-isolated: imports nothing from the storage/catalog layer and makes no
# storage calls. It only turns text into an AST. Existence/type/arity checks are
# deferred to the binder (v3.0); the parser checks structure only.

COMPARISONS = frozenset({"=", "!=", "<", "<=", ">", ">="})
COLUMN_TYPES = frozenset({"INT", "TEXT"})
FUNCTIONS = frozenset({"COUNT", "SUM", "MIN", "MAX", "AVG", "LENGTH"})

# Cap WHERE-expression paren nesting AND boolean-chain length so pathological
# input fails as a ParseError instead of a RecursionError downstream (the binder
# and AST printers walk this tree). No real query approaches these.
MAX_EXPR_DEPTH = 100
MAX_EXPR_TERMS = 500


def parse(sql: str) -> Statement:
    """Parse one SQL statement. Raises ParseError (with position) on malformed
    input. Never raises any other exception type."""
    return _Parser(tokenize(sql)).parse_statement()


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
        self.depth = 0  # WHERE-expression paren nesting
        self.terms = 0  # WHERE-expression boolean-chain length

    # --- cursor helpers ---

    @property
    def cur(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def at(self, kind: str, value: str | None = None) -> bool:
        t = self.cur
        return t.kind == kind and (value is None or t.value == value)

    def expect(self, kind: str, value: str | None = None) -> Token:
        if self.cur.kind == "ERROR":
            self.fail(f"unexpected character {self.cur.value!r}")
        if not self.at(kind, value):
            want = value or kind.lower()
            self.fail(f"expected {want}")
        return self.advance()

    def fail(self, what: str) -> None:
        t = self.cur
        near = "end of input" if t.kind == "EOF" else repr(t.value)
        raise ParseError(f"{what} near {near}", t.pos)

    # --- statement dispatch ---

    def parse_statement(self) -> Statement:
        if self.at("KEYWORD", "CREATE"):
            stmt = self.create_table()
        elif self.at("KEYWORD", "INSERT"):
            stmt = self.insert()
        elif self.at("KEYWORD", "SELECT"):
            stmt = self.select()
        else:
            self.fail("expected CREATE, INSERT, or SELECT")
        if self.at("PUNCT", ";"):
            self.advance()
        if not self.at("EOF"):
            self.fail("unexpected trailing input")
        return stmt

    # --- CREATE TABLE ---

    def create_table(self) -> CreateTable:
        self.expect("KEYWORD", "CREATE")
        self.expect("KEYWORD", "TABLE")
        table = self.expect("IDENT").value
        self.expect("PUNCT", "(")
        columns = [self.column_def()]
        while self.at("PUNCT", ","):
            self.advance()
            columns.append(self.column_def())
        self.expect("PUNCT", ")")
        return CreateTable(table, tuple(columns))

    def column_def(self) -> ColumnDef:
        name = self.expect("IDENT").value
        type_tok = self.expect("IDENT")  # INT/TEXT are identifiers, not keywords
        col_type = type_tok.value.upper()
        if col_type not in COLUMN_TYPES:
            raise ParseError(
                f"unsupported column type {type_tok.value!r} (expected INT or TEXT)",
                type_tok.pos,
            )
        return ColumnDef(name, col_type)

    # --- INSERT ---

    def insert(self) -> Insert:
        self.expect("KEYWORD", "INSERT")
        self.expect("KEYWORD", "INTO")
        table = self.expect("IDENT").value
        self.expect("KEYWORD", "VALUES")
        self.expect("PUNCT", "(")
        values = [self.literal()]
        while self.at("PUNCT", ","):
            self.advance()
            values.append(self.literal())
        self.expect("PUNCT", ")")
        return Insert(table, tuple(values))

    # --- SELECT ---

    def select(self) -> Select:
        self.expect("KEYWORD", "SELECT")
        projections = self.projections()
        self.expect("KEYWORD", "FROM")
        table = self.expect("IDENT").value

        join = None
        if self.at("KEYWORD", "JOIN"):
            join = self.join_clause()

        where = None
        if self.at("KEYWORD", "WHERE"):
            self.advance()
            where = self.expression()

        group_by = None
        if self.at("KEYWORD", "GROUP"):
            self.advance()
            self.expect("KEYWORD", "BY")
            group_by = self.expect("IDENT").value

        order_by = None
        if self.at("KEYWORD", "ORDER"):
            self.advance()
            self.expect("KEYWORD", "BY")
            order_by = self.order_by()

        limit = None
        if self.at("KEYWORD", "LIMIT"):
            self.advance()
            limit = self.limit_value()

        return Select(projections, table, join, where, group_by, order_by, limit)

    def join_clause(self) -> Join:
        self.expect("KEYWORD", "JOIN")
        table = self.expect("IDENT").value
        self.expect("KEYWORD", "ON")
        left = self.column_ref()
        self.expect("OP", "=")  # equi-join only
        right = self.column_ref()
        return Join(table, left, right)

    def projections(self) -> tuple | None:
        if self.at("STAR"):
            self.advance()
            return None  # SELECT *
        items = [self.select_item()]
        while self.at("PUNCT", ","):
            self.advance()
            items.append(self.select_item())
        return tuple(items)

    def select_item(self):
        # A function call (COUNT(*), SUM(x), LENGTH(x)) or a column reference.
        if self.at("IDENT") and self.tokens[self.i].value.upper() in FUNCTIONS \
                and self.tokens[self.i + 1].value == "(":
            return self.func_call()
        return self.column_ref()

    def func_call(self) -> FuncCall:
        name_tok = self.expect("IDENT")
        name = name_tok.value.upper()
        self.expect("PUNCT", "(")
        if self.at("STAR"):
            self.advance()
            arg = None  # COUNT(*)
        else:
            arg = self.column_ref()
        self.expect("PUNCT", ")")
        return FuncCall(name, arg)

    def column_ref(self) -> ColumnRef:
        first = self.expect("IDENT").value
        if self.at("PUNCT", "."):
            self.advance()
            col = self.expect("IDENT").value
            return ColumnRef(col, table=first)  # qualified: table.col
        return ColumnRef(first)

    def order_by(self) -> OrderBy:
        column = self.expect("IDENT").value
        descending = False
        if self.at("KEYWORD", "ASC"):
            self.advance()
        elif self.at("KEYWORD", "DESC"):
            self.advance()
            descending = True
        return OrderBy(column, descending)

    def limit_value(self) -> int:
        tok = self.expect("INT")
        n = int(tok.value)
        if n < 0:
            raise ParseError("LIMIT must not be negative", tok.pos)
        return n

    # --- expressions (precedence: OR < AND < comparison < primary) ---

    def expression(self) -> object:
        return self.or_expr()

    def or_expr(self) -> object:
        left = self.and_expr()
        while self.at("KEYWORD", "OR"):
            self.advance()
            self._count_term()
            left = BoolOp("OR", left, self.and_expr())
        return left

    def and_expr(self) -> object:
        left = self.comparison()
        while self.at("KEYWORD", "AND"):
            self.advance()
            self._count_term()
            left = BoolOp("AND", left, self.comparison())
        return left

    def _count_term(self) -> None:
        self.terms += 1
        if self.terms > MAX_EXPR_TERMS:
            self.fail("expression too long")

    def comparison(self) -> object:
        left = self.primary()
        if self.cur.kind == "OP" and self.cur.value in COMPARISONS:
            op = self.advance().value
            return Comparison(op, left, self.primary())
        return left

    def primary(self) -> object:
        if self.at("PUNCT", "("):
            if self.depth >= MAX_EXPR_DEPTH:
                self.fail("expression nested too deeply")
            self.depth += 1
            self.advance()
            expr = self.expression()
            self.expect("PUNCT", ")")
            self.depth -= 1
            return expr
        if self.at("IDENT"):
            if self.cur.value.upper() in FUNCTIONS and self.tokens[self.i + 1].value == "(":
                return self.func_call()
            return self.column_ref()  # bare or qualified (table.col)
        if self.cur.kind in ("INT", "STRING") or self.at("KEYWORD", "NULL"):
            return self.literal()
        self.fail("expected an expression")

    def literal(self) -> Literal:
        t = self.cur
        if t.kind == "INT":
            self.advance()
            return Literal("INT", int(t.value))
        if t.kind == "STRING":
            self.advance()
            return Literal("STRING", t.value)
        if self.at("KEYWORD", "NULL"):
            self.advance()
            return Literal("NULL", None)
        self.fail("expected a value (number, 'string', or NULL)")
