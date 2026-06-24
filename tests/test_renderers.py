"""Tests for renderer Protocol, PlainRenderer, and RichRenderer."""

from superdb.render.plain import PlainRenderer
from superdb.render.protocol import Renderer


def test_plain_renderer_message_to_stdout(capsys):
    PlainRenderer().render_message("hi")
    out, err = capsys.readouterr()
    assert out == "hi\n"
    assert err == ""


def test_plain_renderer_error_to_stderr(capsys):
    PlainRenderer().render_error("boom")
    out, err = capsys.readouterr()
    assert out == ""
    assert "Error: boom" in err


def test_plain_renderer_satisfies_protocol():
    r: Renderer = PlainRenderer()
    assert callable(r.render_message)
    assert callable(r.render_error)


def test_rich_renderer_message_to_stdout(capsys):
    from superdb.render.rich import RichRenderer

    RichRenderer().render_message("hi")
    out, err = capsys.readouterr()
    assert "hi" in out


def test_rich_renderer_preserves_bracketed_cell_values(capsys):
    # Regression: TEXT values like "[red]x[/red]" must render literally, not be
    # parsed as Rich markup and stripped (silent data corruption in the display).
    from superdb.render.rich import RichRenderer

    RichRenderer().render_result(("body",), [{"body": "[red]hacked[/red]"}])
    out, _ = capsys.readouterr()
    assert "[red]hacked[/red]" in out


def test_rich_renderer_error_with_markup_does_not_crash(capsys):
    # Regression: a message containing malformed markup like "[/]" must not raise
    # rich.errors.MarkupError out of the error path.
    from superdb.render.rich import RichRenderer

    RichRenderer().render_error("bad column spec 'id[/]INT'")
    _, err = capsys.readouterr()
    assert "id[/]INT" in err
