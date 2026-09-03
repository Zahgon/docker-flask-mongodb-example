import random, json, datetime, sys

from typing import Optional

import uvicorn
from fastapi import FastAPI, Form, Query
from fastapi.responses import Response
from pymongo import MongoClient
from bson import json_util

from utils import get_logger
from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
mongo_host = "mongodb"
if len(sys.argv) == 2:
    mongo_host = sys.argv[1]
random_numbers = MongoClient(mongo_host, 27017).demo.random_numbers
logger = get_logger()


@app.put("/random")
def random_insert(lower: Optional[str] = Form(None), upper: Optional[str] = Form(None)):
    """Add a number number to the list of last 5 numbers"""
    number = str(random.randint(int(lower), int(upper)))
    random_numbers.update_one(
        {"_id": "lasts"},
        {
            "$push": {
                "items": {
                    "$each": [{"value": number, "date": datetime.datetime.utcnow()}],
                    "$sort": {"date": -1},
                    "$slice": 5,
                }
            }
        },
        upsert=True,
    )
    return Response(number, status_code=200, media_type="application/json")


@app.get("/random")
def random_generator(
    lower: Optional[str] = Query(None), upper: Optional[str] = Query(None)
):
    """Returns a random number in interval"""
    lower = int(lower) if lower is not None else 10
    upper = int(upper) if upper is not None else 0
    if upper < lower:
        return Response(
            json.dumps(
                {"error": "Upper boundary must be greater or equal than lower boundary"}
            ),
            status_code=400,
            media_type="application/json",
        )
    number = str(random.randint(lower, upper))
    return Response(number, status_code=200, media_type="application/json")


@app.get("/random-list")
def last_number_list():
    """Gets the latest 5 generated numbers"""
    last_numbers = list(random_numbers.find({"_id": "lasts"}))
    if len(last_numbers) == 0:
        extracted = []
    else:
        extracted = [d["value"] for d in last_numbers[0]["items"]]
    return Response(
        json.dumps(extracted, default=json_util.default),
        status_code=200,
        media_type="application/json",
    )


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    logger.debug("Random demo app started")
    # starts the app in debug mode, bind on all ip's and on port 5000
    uvicorn.run(app, host="0.0.0.0", port=5000)
