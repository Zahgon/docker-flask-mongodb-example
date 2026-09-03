import json

import redis
import uvicorn
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import Response
from pymongo import MongoClient, errors
from bson import json_util
from utils import read_docker_secret
from caching import cache, cache_invalidate
from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
users = MongoClient("mongodb", 27017).demo.users


redis_cache = redis.Redis(
    host="redis", port=6379, db=0, password=read_docker_secret("REDIS_PASSWORD")
)


def serialize_datetime(value: str):
    return datetime.strptime(value, "%Y-%m-%d")


def format_user(user: dict) -> dict:
    if user is None:
        return None
    return {
        "userid": user["_id"],
        "name": user["name"],
        "email": user["email"],
        "birthdate": user["birthdate"].strftime("%Y-%m-%d")
        if "birthdate" in user and user["birthdate"] is not None
        else None,
        "country": user["country"] if "country" in user else None,
    }


@app.post("/users/{userid:int}")
@cache_invalidate(redis=redis_cache, key="userid")
async def add_user(userid: int, request: Request):
    """Create user"""
    request_params = await request.form()
    if "email" not in request_params or "name" not in request_params:
        return Response(
            "Email and name not present in parameters!",
            status_code=404,
            media_type="application/json",
        )
    try:
        users.insert_one(
            {
                "_id": userid,
                "email": request_params["email"],
                "name": request_params["name"],
                "birthdate": serialize_datetime(request_params["birthdate"])
                if "birthdate" in request_params
                else None,
                "country": request_params["country"],
            }
        )
    except errors.DuplicateKeyError as e:
        return Response(
            "Duplicate user id!", status_code=404, media_type="application/json"
        )
    return Response(
        json.dumps(format_user(users.find_one({"_id": userid}))),
        status_code=200,
        media_type="application/json",
    )


@app.put("/users/{userid:int}")
@cache_invalidate(redis=redis_cache, key="userid")
async def update_user(userid: int, request: Request):
    """Update user information"""
    request_params = await request.form()
    set = {}
    if "email" in request_params:
        set["email"] = request_params["email"]
    if "name" in request_params:
        set["name"] = request_params["name"]
    if "birthdate" in request_params:
        set["birthdate"] = (serialize_datetime(request_params["birthdate"]),)
    if "country" in request_params:
        set["country"] = request_params["country"]

    users.update_one({"_id": userid}, {"$set": set})
    return Response(
        json.dumps(format_user(users.find_one({"_id": userid}))),
        status_code=200,
        media_type="application/json",
    )


@app.get("/users/{userid:int}")
@cache(redis=redis_cache, key="userid")
async def get_user(userid: int):
    """Details about a user"""
    user = users.find_one({"_id": userid})

    if None == user:
        return Response("", status_code=404, media_type="application/json")
    return Response(
        json.dumps(format_user(user)), status_code=200, media_type="application/json"
    )


@app.get("/users")
async def get_users(request: Request):
    """Example endpoint returning all users with pagination"""
    request_args = request.query_params
    limit = int(request_args.get("limit")) if "limit" in request_args else 10
    offset = int(request_args.get("offset")) if "offset" in request_args else 0
    user_list = users.find().limit(limit).skip(offset)
    if None == users:
        return Response(json.dumps([]), status_code=200, media_type="application/json")
    extracted = [format_user(d) for d in user_list]

    return Response(
        json.dumps(extracted, default=json_util.default),
        status_code=200,
        media_type="application/json",
    )


@app.delete("/users/{userid:int}")
@cache_invalidate(redis=redis_cache, key="userid")
async def delete_user(userid: int):
    """Delete operation for a user"""
    users.delete_one({"_id": userid})
    return Response("", status_code=200, media_type="application/json")


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
