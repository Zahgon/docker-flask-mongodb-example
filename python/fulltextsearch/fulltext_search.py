import sys
import json, datetime
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient, TEXT
from bson import json_util

from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
basic_auth = HTTPBasic(auto_error=False)
users = {
    "admin": generate_password_hash("changeme"),
}


class UnauthorizedAccess(Exception):
    pass


@app.exception_handler(UnauthorizedAccess)
async def unauthorized_access(request: Request, exc: UnauthorizedAccess) -> Response:
    return Response(
        "Unauthorized Access",
        status_code=401,
        media_type="text/html",
        headers={"WWW-Authenticate": 'Basic realm="Authentication Required"'},
    )


def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username


def login_required(
    credentials: Optional[HTTPBasicCredentials] = Depends(basic_auth),
) -> str:
    if credentials is None:
        raise UnauthorizedAccess()
    username = verify_password(credentials.username, credentials.password)
    if not username:
        raise UnauthorizedAccess()
    return username


mongo_host = "mongodb"
if len(sys.argv) == 2:
    mongo_host = sys.argv[1]
fulltext_search = MongoClient(mongo_host, 27017).demo.fulltext_search


@app.get("/search/{searched_expression}")
async def search(searched_expression: str, username: str = Depends(login_required)):
    """Search by an expression"""
    results = (
        fulltext_search.find(
            {"$text": {"$search": searched_expression}},
            {"score": {"$meta": "textScore"}},
        )
        .sort([("score", {"$meta": "textScore"})])
        .limit(10)
    )
    results = [
        {"text": result["app_text"], "date": result["indexed_date"].isoformat()}
        for result in results
    ]
    return Response(
        json.dumps(list(results), default=json_util.default),
        status_code=200,
        media_type="application/json",
    )


@app.put("/fulltext")
async def add_expression(request: Request, username: str = Depends(login_required)):
    """Add an expression to fulltext index"""
    request_params = await request.form()
    if "expression" not in request_params:
        return Response(
            '"Expression" must be present as a POST parameter!',
            status_code=404,
            media_type="application/json",
        )
    document = {
        "app_text": request_params["expression"],
        "indexed_date": datetime.datetime.utcnow(),
    }
    fulltext_search.save(document)
    return Response(
        json.dumps(document, default=json_util.default),
        status_code=200,
        media_type="application/json",
    )


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    # create the fulltext index
    fulltext_search.create_index(
        [("app_text", TEXT)], name="fulltextsearch_index", default_language="english"
    )
    # starts the app in debug mode, bind on all ip's and on port 5000
    uvicorn.run(app, host="0.0.0.0", port=5000)
