"""Xpense AI was removed on purpose; keep it from coming back through a merge."""
import importlib.util


def test_assistant_route_and_package_are_gone(signed_in, app):
    assert signed_in.get("/assistant/").status_code == 404
    assert "assistant" not in app.blueprints
    assert importlib.util.find_spec("app.assistant") is None
    assert "OPENAI_API_KEY" not in app.config


def test_pages_have_no_ai_link(signed_in):
    for path in ("/", "/calendar", "/commitments", "/expenses/"):
        page = signed_in.get(path, follow_redirects=True)
        assert page.status_code == 200
        assert "/assistant" not in page.text
        assert "Xpense AI" not in page.text
