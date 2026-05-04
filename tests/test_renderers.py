"""Tests for renderer Protocol, PlainRenderer, and RichRenderer."""
import pytest

from super_db.render.plain_renderer import PlainRenderer
from super_db.render.protocol import Renderer


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
    from super_db.render.rich_renderer import RichRenderer

    RichRenderer().render_message("hi")
    out, err = capsys.readouterr()
    assert "hi" in out
