import json, sys, uuid, datetime

import jwt as pyjwt
import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from pymongo import MongoClient, GEOSPHERE
from bson import json_util

from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
mongo_host = "mongodb"
if len(sys.argv) == 2:
    mongo_host = sys.argv[1]
places = MongoClient(mongo_host, 27017).demo.places

JWT_AUTH_URL_RULE = "/api/auth"
JWT_SECRET_KEY = "super-secret"
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRES = datetime.timedelta(minutes=15)


class User(object):
    def __init__(self, user_id, username, password):
        self.id = user_id
        self.username = username
        self.password = password

    def __str__(self):
        return "User(id='%s')" % self.id


users = [
    User(1, "admin", "secret"),
]


class NoAuthorizationError(Exception):
    def __init__(self, message):
        self.message = message


class InvalidTokenError(Exception):
    def __init__(self, message):
        self.message = message


def jsonify(payload, status_code: int = 200) -> Response:
    return Response(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        status_code=status_code,
        media_type="application/json",
    )


@app.exception_handler(NoAuthorizationError)
async def no_authorization(request: Request, exc: NoAuthorizationError) -> Response:
    return jsonify({"msg": exc.message}, 401)


@app.exception_handler(InvalidTokenError)
async def invalid_token(request: Request, exc: InvalidTokenError) -> Response:
    return jsonify({"msg": exc.message}, 422)


def create_access_token(identity):
    now = datetime.datetime.now(datetime.timezone.utc)
    return pyjwt.encode(
        {
            "fresh": False,
            "iat": now,
            "jti": str(uuid.uuid4()),
            "type": "access",
            "sub": identity,
            "nbf": now,
            "exp": now + JWT_ACCESS_TOKEN_EXPIRES,
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def jwt_required(request: Request) -> str:
    auth_header = request.headers.get("Authorization", None)
    if not auth_header:
        raise NoAuthorizationError("Missing Authorization Header")
    parts = auth_header.split()
    if parts[0] != "Bearer":
        raise NoAuthorizationError(
            "Missing 'Bearer' type in 'Authorization' header. "
            "Expected 'Authorization: Bearer <JWT>'"
        )
    if len(parts) != 2:
        raise NoAuthorizationError(
            "Bad Authorization header. Expected 'Authorization: Bearer <JWT>'"
        )
    try:
        decoded = pyjwt.decode(parts[1], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except pyjwt.PyJWTError as e:
        raise InvalidTokenError(str(e))
    return decoded["sub"]


@app.post("/login")
async def login(request: Request):
    """User authenticate method."""
    try:
        request_params = await request.form()
        username = request_params.get("username", None)
        password = request_params.get("password", None)
        authenticated_user = [
            user
            for user in users
            if username == user.username and password == user.password
        ]
        if not authenticated_user:
            return jsonify({"msg": "Bad username or password"}, 401)

        access_token = create_access_token(identity=username)
        resp = jsonify({"access_token": "Bearer {0}".format(access_token)})
    except Exception as e:
        resp = jsonify({"message": "Bad username and/or password"}, 401)
    return resp


@app.post("/location")
async def new_location(request: Request, identity: str = Depends(jwt_required)):
    """Add a place (name, latitude and longitude)"""
    request_params = await request.form()
    if (
        "name" not in request_params
        or "lat" not in request_params
        or "lng" not in request_params
    ):
        return Response(
            "Name, lat, lng must be present in parameters!",
            status_code=404,
            media_type="application/json",
        )
    latitude = float(request_params["lng"])
    longitude = float(request_params["lat"])
    places.insert_one(
        {
            "name": request_params["name"],
            "location": {"type": "Point", "coordinates": [latitude, longitude]},
        }
    )
    return Response(
        json.dumps({"name": request_params["name"], "lat": latitude, "lng": longitude}),
        status_code=200,
        media_type="application/json",
    )


@app.get("/location/{lat}/{lng}")
async def get_near(
    lat: str, lng: str, request: Request, identity: str = Depends(jwt_required)
):
    """Get all points near a location given coordonates, and radius"""
    max_distance = int(request.query_params.get("max_distance", 10000))
    limit = int(request.query_params.get("limit", 10))
    cursor = places.find(
        {
            "location": {
                "$near": {
                    "$geometry": {
                        "type": "Point",
                        "coordinates": [float(lng), float(lat)],
                    },
                    "$maxDistance": max_distance,
                }
            }
        }
    ).limit(limit)
    extracted = [
        {
            "name": d["name"],
            "lat": d["location"]["coordinates"][1],
            "lng": d["location"]["coordinates"][0],
        }
        for d in cursor
    ]
    return Response(
        json.dumps(extracted, default=json_util.default),
        status_code=200,
        media_type="application/json",
    )


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    # cretes a GEOSHPHERE (2dsphere in MongoDb: https://docs.mongodb.com/manual/core/2dsphere/) index
    # named "location_index" on "location" field, it's used to search by distance
    places.create_index([("location", GEOSPHERE)], name="location_index")

    # starts the app in debug mode, bind on all ip's and on port 5000
    uvicorn.run(app, host="0.0.0.0", port=5000)
