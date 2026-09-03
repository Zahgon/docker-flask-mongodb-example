import os
import pickle
import uuid
from tempfile import mkdtemp

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
SESSION_FILE_DIR = mkdtemp()
SESSION_PERMANENT = False
SESSION_TYPE = "filesystem"
SESSION_COOKIE_NAME = "session"

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
)
templates.env.globals["url_for"] = lambda name, **params: app.url_path_for(
    name, **params
)

REDIRECT_BODY = (
    "<!doctype html>\n"
    "<html lang=en>\n"
    "<title>Redirecting...</title>\n"
    "<h1>Redirecting...</h1>\n"
    "<p>You should be redirected automatically to the target URL: "
    '<a href="{location}">{location}</a>. If not, click the link.\n'
)


def session_path(session_id: str) -> str:
    return os.path.join(SESSION_FILE_DIR, session_id)


def load_session(session_id: str) -> dict:
    try:
        with open(session_path(session_id), "rb") as stored:
            return pickle.load(stored)
    except (OSError, EOFError, pickle.UnpicklingError):
        return {}


def store_session(session_id: str, session: dict) -> None:
    with open(session_path(session_id), "wb") as stored:
        pickle.dump(session, stored)


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id is None:
        session_id = str(uuid.uuid4())
        session = {}
    else:
        session = load_session(session_id)
    request.state.session = session
    response = await call_next(request)
    store_session(session_id, request.state.session)
    response.headers.append(
        "set-cookie", "%s=%s; HttpOnly; Path=/" % (SESSION_COOKIE_NAME, session_id)
    )
    return response


def redirect(request: Request, location: str) -> Response:
    """Redirect to *location* as Werkzeug does: a relative Location header.

    Building an absolute URL from ``request.base_url`` would advertise the
    host the service happens to be reached on, which behind the gateway is the
    internal one.
    """
    return Response(
        REDIRECT_BODY.format(location=location),
        status_code=302,
        media_type="text/html",
        headers={"Location": location},
    )


class Game:
    WIN_LINES = [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 9],  # horiz.
        [1, 4, 7],
        [2, 5, 8],
        [3, 6, 9],  # vertical
        [1, 5, 9],
        [3, 5, 7],  # diagonal
    ]

    def has_won(self, board: list, turn: str) -> bool:
        wins = [all([(board[c - 1] == turn) for c in line]) for line in self.WIN_LINES]
        return any(wins)

    def has_moves_left(self, board: list) -> bool:
        return all([move is not None for move in board])

    def get_next_player(self, turn: str):
        return {"O": "X", "X": "O"}[turn]


game = Game()


def initiate_session(session):
    session["board"] = [None, None, None, None, None, None, None, None, None]
    session["turn"] = "X"
    session["winner"] = False
    session["draw"] = False


@app.get("/")
def index(request: Request):
    session = request.state.session
    if "board" not in session:
        initiate_session(session)
    winner_x = game.has_won(session["board"], "X")
    winner_O = game.has_won(session["board"], "O")
    if winner_x or winner_O:
        session["winner"] = True
        session["turn"] = "X" if winner_x else "O"
    if game.has_moves_left(session["board"]):
        session["draw"] = True
    return templates.TemplateResponse(
        "tictactoe.html",
        {
            "request": request,
            "game": session["board"],
            "turn": session["turn"],
            "winnerFound": session["winner"],
            "winner": session["turn"],
            "draw": session["draw"],
        },
    )


@app.get("/play/{row:int}/{col:int}")
def play(request: Request, row: int, col: int):
    session = request.state.session
    session["board"][col * 3 + row] = session["turn"]
    session["turn"] = game.get_next_player(session["turn"])
    return redirect(request, app.url_path_for("index"))


@app.get("/reset")
def reset(request: Request):
    initiate_session(request.state.session)
    return redirect(request, app.url_path_for("index"))


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
