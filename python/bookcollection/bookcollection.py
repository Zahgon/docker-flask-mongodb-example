import sys
import json
import requests
import dateutil.parser
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pymongo import MongoClient, errors

from utils import get_logger
from http_compat import JsonBodyError, add_automatic_methods, install_error_pages, read_json


if len(sys.argv) == 3:
    _, users_host, mongo_host = sys.argv
    mongo_client = MongoClient(mongo_host, 27017)
else:
    users_host = "http://web-users:5000"
    mongo_client = MongoClient("mongodb", 27017)
bookcollection = mongo_client.demo.bookcollection
borrowcollection = mongo_client.demo.borrowcollection
logger = get_logger()


app = FastAPI(
    title="Book collection",
    description="Simulates a book library with users and book borrwing",
)
install_error_pages(app)


@app.exception_handler(JsonBodyError)
async def json_body_error_handler(request: Request, exc: JsonBodyError) -> Response:
    """flask-restx rendered a rejected payload as its own ``message`` envelope."""
    return Response(
        json.dumps({"message": exc.description}, indent=4) + "\n",
        status_code=exc.status_code,
        media_type="application/json",
    )


class User:
    def __init__(self, exists: bool, userid: int, name: str, email: str) -> None:
        self.exists = exists
        self.userid = userid
        self.name = name
        self.email = email


class PaginationError(Exception):
    def __init__(self, errors: dict) -> None:
        self.errors = errors


pagination_arguments = {"limit": "Limit", "offset": "Offset"}


def parse_pagination(request: Request) -> dict:
    args = {}
    errors = {}
    for name, help in pagination_arguments.items():
        value = request.query_params.get(name)
        if value is None:
            args[name] = None
            continue
        try:
            args[name] = int(value)
        except ValueError as e:
            errors[name] = "{0} {1}".format(help, str(e))
    if errors:
        raise PaginationError(errors)
    return args


@app.exception_handler(PaginationError)
async def pagination_error_handler(request: Request, exc: PaginationError) -> Response:
    return Response(
        json.dumps(
            {"errors": exc.errors, "message": "Input payload validation failed"},
            indent=4,
        )
        + "\n",
        status_code=400,
        media_type="application/json",
    )


def marshal_list(data: list, fields: dict) -> str:
    return (
        json.dumps(
            [
                {name: cast(entry[name]) for name, cast in fields.items()}
                for entry in data
            ],
            indent=4,
        )
        + "\n"
    )


def as_string(value):
    return None if value is None else str(value)


def as_integer(value):
    return None if value is None else int(value)


def as_datetime(value):
    return None if value is None else value.isoformat()


def get_user(id: int) -> User:
    try:
        response = requests.get(url="{0}/users/{1}".format(users_host, str(id)))
    except Exception as e:
        logger.error("Error getting user data error: {0}".format(str(e)))
        return User(False, id, None, None)
    if response.status_code != 200:
        return User(False, id, None, None)
    try:
        result = response.json()
        return User(True, id, result["name"], result["email"])
    except:
        return User(False, id, None, None)


@app.put("/borrow/return/{id}")
async def return_borrow(id: str, request: Request):
    """Returns a borrowed book"""
    payload = await read_json(request)
    payload["id"] = id
    borrow = borrowcollection.find_one({"id": id})
    if None is borrow:
        return Response(
            json.dumps({"error": "Borrow id not found"}),
            status_code=404,
            media_type="application/json",
        )
    if "return_date" in borrow:
        return Response(
            json.dumps({"error": "Book already returned"}),
            status_code=404,
            media_type="application/json",
        )
    del borrow["_id"]
    bookcollection.update_one({"isbn": borrow["isbn"]}, {"$inc": {"nr_available": 1}})
    borrowcollection.update_one(
        {"id": payload["id"]},
        {"$set": {"return_date": dateutil.parser.parse(payload["return_date"])}},
    )
    return Response(
        json.dumps(payload, default=str),
        status_code=200,
        media_type="application/json",
    )


@app.get("/borrow/{id}")
async def get_borrow(id: str):
    """Returns a borrow by id"""
    borrow = borrowcollection.find_one({"id": id})
    if None is borrow:
        return Response(
            json.dumps({"error": "Borrow id not found"}),
            status_code=404,
            media_type="application/json",
        )
    del borrow["_id"]
    user = get_user(borrow["userid"])
    borrow["user_name"] = user.name
    borrow["user_email"] = user.email
    book = bookcollection.find_one({"isbn": borrow["isbn"]})
    if None is book:
        return Response(
            json.dumps({"error": "Book not found"}),
            status_code=404,
            media_type="application/json",
        )
    borrow["book_name"] = book["name"]
    borrow["book_author"] = book["author"]
    return Response(
        json.dumps(borrow, default=str),
        status_code=200,
        media_type="application/json",
    )


@app.put("/borrow/{id}")
async def borrow_book(id: str, request: Request):
    """Borrows a book"""
    payload = await read_json(request)
    session = mongo_client.start_session()
    session.start_transaction()
    try:
        borrow = borrowcollection.find_one({"id": id}, session=session)
        if None is not borrow:
            return Response(
                json.dumps({"error": "Borrow already used"}),
                status_code=404,
                media_type="application/json",
            )
        payload["id"] = id
        user = get_user(payload["userid"])
        if not user.exists:
            return Response(
                json.dumps({"error": "User not found"}),
                status_code=404,
                media_type="application/json",
            )
        book = bookcollection.find_one({"isbn": payload["isbn"]}, session=session)
        if book is None:
            return Response(
                json.dumps({"error": "Book not found"}),
                status_code=404,
                media_type="application/json",
            )
        if book["nr_available"] < 1:
            return Response(
                json.dumps({"error": "Book is not available yet"}),
                status_code=404,
                media_type="application/json",
            )
        payload["borrow_date"] = dateutil.parser.parse(payload["borrow_date"])
        payload["max_return_date"] = dateutil.parser.parse(payload["max_return_date"])
        payload.pop("return_date", None)
        borrowcollection.insert_one(payload, session=session)
        bookcollection.update_one(
            {"isbn": payload["isbn"]},
            {"$inc": {"nr_available": -1}},
            session=session,
        )
        del payload["_id"]
        db_entry = borrowcollection.find_one({"id": id}, session=session)
        session.commit_transaction()
    except Exception as e:
        session.end_session()
        return Response(
            json.dumps({"error": str(e)}, default=str),
            status_code=500,
            media_type="application/json",
        )

    session.end_session()
    return Response(
        json.dumps(db_entry, default=str),
        status_code=200,
        media_type="application/json",
    )


@app.get("/borrow")
async def list_borrows(request: Request):
    """Lists the borrows"""
    args = parse_pagination(request)
    data = (
        borrowcollection.find().sort("id", 1).limit(args["limit"]).skip(args["offset"])
    )
    extracted = [
        {
            "id": d["id"],
            "userid": d["userid"],
            "isbn": d["isbn"],
            "borrow_date": d["borrow_date"],
            "return_date": d["return_date"] if "return_date" in d else None,
            "max_return_date": d["max_return_date"],
        }
        for d in data
    ]
    return Response(
        marshal_list(
            extracted,
            {
                "id": as_string,
                "userid": as_integer,
                "isbn": as_string,
                "borrow_date": as_datetime,
                "return_date": as_datetime,
                "max_return_date": as_datetime,
            },
        ),
        status_code=200,
        media_type="application/json",
    )


@app.get("/book/{isbn}")
async def get_book(isbn: str):
    """Returns a book by isbn"""
    book = bookcollection.find_one({"isbn": isbn})
    if None is book:
        return Response(
            json.dumps({"error": "Book not found"}),
            status_code=404,
            media_type="application/json",
        )
    del book["_id"]
    return Response(json.dumps(book), status_code=200, media_type="application/json")


@app.put("/book/{isbn}")
async def add_book(isbn: str, request: Request):
    """Adds a book"""
    payload = await read_json(request)
    payload["isbn"] = isbn
    try:
        bookcollection.insert_one(payload)
    except errors.DuplicateKeyError:
        return Response(
            json.dumps({"error": "Isbn already exists"}),
            status_code=404,
            media_type="application/json",
        )
    del payload["_id"]
    return Response(json.dumps(payload), status_code=200, media_type="application/json")


@app.delete("/book/{isbn}")
async def delete_book(isbn: str):
    """Deletes a book by isbn"""
    bookcollection.delete_one({"isbn": isbn})
    return Response("", status_code=200, media_type="application/json")


@app.get("/book")
async def list_books(request: Request):
    """Lists the books"""
    args = parse_pagination(request)
    books = (
        bookcollection.find().sort("id", 1).limit(args["limit"]).skip(args["offset"])
    )
    extracted = [
        {
            "isbn": d["isbn"],
            "name": d["name"],
            "author": d["author"],
            "publisher": d["publisher"],
            "nr_available": d["nr_available"],
        }
        for d in books
    ]
    return Response(
        marshal_list(
            extracted,
            {
                "isbn": as_string,
                "name": as_string,
                "author": as_string,
                "publisher": as_string,
                "nr_available": as_integer,
            },
        ),
        status_code=200,
        media_type="application/json",
    )


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    try:
        mongo_client.admin.command("replSetInitiate")
    except errors.OperationFailure as e:
        logger.error("Error setting mongodb replSetInitiate error: {0}".format(str(e)))
    bookcollection.insert_one({"isbn": 0})
    bookcollection.delete_one({"isbn": 0})
    borrowcollection.insert_one({"id": 0})
    borrowcollection.delete_one({"id": 0})

    bookcollection.create_index("isbn", unique=True)
    # starts the app in debug mode, bind on all ip's and on port 5000
    uvicorn.run(app, host="0.0.0.0", port=5000)
