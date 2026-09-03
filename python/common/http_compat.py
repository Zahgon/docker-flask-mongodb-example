"""Pieces of the Flask/Werkzeug HTTP contract that Starlette does not provide.

The services in this repository were migrated from Flask to FastAPI.  Three
externally visible parts of the original contract have no Starlette equivalent,
so they are re-implemented here once and shared by every service:

* Werkzeug serves ``text/html`` error pages for 404 and 405; Starlette serves
  ``{"detail": ...}`` JSON.
* Werkzeug's 405 advertises every method registered for the matched *rule*.
  Starlette registers one route per method and reports the methods of the first
  route whose path matched, which truncates ``Allow`` to a single method and
  violates RFC 9110 section 10.2.1.
* ``flask.Request.get_json()`` requires an ``application/json`` media type (415)
  and converts a decode failure into a 400.  ``await request.json()`` does
  neither: it parses any media type and lets ``JSONDecodeError`` escape as a 500.

Nothing here imports Flask or Werkzeug; this is a small self-contained
re-implementation of the behaviour their responses promised to clients.
"""

import json
from http import HTTPStatus

from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match, Route

NOT_FOUND_DESCRIPTION = (
    "The requested URL was not found on the server. If you entered the URL"
    " manually please check your spelling and try again."
)
METHOD_NOT_ALLOWED_DESCRIPTION = "The method is not allowed for the requested URL."
UNSUPPORTED_MEDIA_TYPE_DESCRIPTION = (
    "Did not attempt to load JSON data because the request Content-Type was not"
    " 'application/json'."
)

# MarkupSafe's escape table, which Werkzeug uses to render error pages.  Note
# that it emits ``&#39;`` for an apostrophe where ``html.escape`` emits
# ``&#x27;``; the difference is visible in the 415 body.
_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&#34;"),
    ("'", "&#39;"),
)


def escape(value: str) -> str:
    """Escape *value* the way MarkupSafe (and therefore Werkzeug) does."""
    for char, replacement in _ESCAPES:
        value = value.replace(char, replacement)
    return value


def html_error(status_code: int, description: str, headers: dict = None) -> Response:
    """Render Werkzeug's ``HTTPException`` HTML page for *status_code*."""
    name = HTTPStatus(status_code).phrase
    body = (
        "<!doctype html>\n"
        "<html lang=en>\n"
        "<title>{0} {1}</title>\n"
        "<h1>{1}</h1>\n"
        "<p>{2}</p>\n"
    ).format(status_code, escape(name), escape(description))
    return Response(
        body, status_code=status_code, media_type="text/html", headers=headers
    )


def matching_methods(request: Request) -> set:
    """Every method registered on a route whose path matches this request.

    Starlette splits one Flask rule across several routes, so the methods have
    to be unioned back together to produce a complete ``Allow`` header.
    """
    methods = set()
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match is Match.NONE:
            continue
        methods.update(getattr(route, "methods", None) or ())
    return methods


def install_error_pages(app) -> None:
    """Serve Werkzeug-style 404/405 pages, with a complete ``Allow`` header.

    Only the two router-level statuses are re-rendered; anything else an
    ``HTTPException`` carries is left to FastAPI's own handler.
    """

    @app.exception_handler(StarletteHTTPException)
    async def _error_page(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return html_error(404, NOT_FOUND_DESCRIPTION)
        if exc.status_code == 405:
            methods = sorted(matching_methods(request))
            headers = {"Allow": ", ".join(methods)} if methods else None
            return html_error(405, METHOD_NOT_ALLOWED_DESCRIPTION, headers)
        return await http_exception_handler(request, exc)


def _options_endpoint(allow: list):
    """Werkzeug's automatic OPTIONS response: 200, ``Allow``, and no body.

    ``Content-Length: 0`` is set explicitly rather than left to Starlette: older
    releases test the body for truthiness, so an empty one yields no header at
    all and the response goes out chunked.  These services do not all pin the
    same Starlette, so stating it keeps them consistent with each other and with
    Werkzeug.
    """
    headers = {"Allow": ", ".join(allow), "Content-Length": "0"}

    async def options(request: Request) -> Response:
        return Response(b"", status_code=200, media_type="text/html", headers=headers)

    return options


def add_automatic_methods(app) -> None:
    """Give every rule the automatic HEAD and OPTIONS that Werkzeug provides.

    Werkzeug answers HEAD by dispatching to the GET view and dropping the body,
    and answers OPTIONS from the router itself -- without calling the view, so
    an authenticated endpoint still answers OPTIONS unauthenticated.  Starlette's
    ``Route`` adds HEAD but FastAPI's ``APIRoute`` does not, and neither adds
    OPTIONS, so both are restored here.

    Call this once, after every route has been registered.
    """
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods and "GET" in methods:
            methods.add("HEAD")

    by_path = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or not methods:
            continue
        by_path.setdefault(path, set()).update(methods)

    for path, methods in sorted(by_path.items()):
        if "OPTIONS" in methods:
            continue
        endpoint = _options_endpoint(sorted(methods | {"OPTIONS"}))
        app.router.routes.append(Route(path, endpoint, methods=["OPTIONS"]))


class JsonBodyError(Exception):
    """A request body that ``flask.Request.get_json()`` would have rejected."""

    def __init__(self, status_code: int, description: str) -> None:
        self.status_code = status_code
        self.description = description


def _is_json(content_type: str) -> bool:
    """Werkzeug's ``Request.is_json``: the JSON media type, or a ``+json`` suffix."""
    mimetype = (content_type or "").split(";", 1)[0].strip().lower()
    return mimetype == "application/json" or (
        mimetype.startswith("application/") and mimetype.endswith("+json")
    )


async def read_json(request: Request):
    """``flask.Request.get_json()`` semantics.

    Raises :class:`JsonBodyError` with 415 if the media type is not JSON, or 400
    if the body does not parse.  Each service renders that in its own error
    envelope, since flask-restx and plain Flask did not share one.
    """
    if not _is_json(request.headers.get("content-type")):
        raise JsonBodyError(415, UNSUPPORTED_MEDIA_TYPE_DESCRIPTION)
    body = await request.body()
    try:
        return json.loads(body)
    except ValueError as exc:
        raise JsonBodyError(400, "Failed to decode JSON object: {0}".format(exc))
