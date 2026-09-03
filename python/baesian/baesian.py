import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from pymongo import MongoClient

from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
baesian = MongoClient("mongodb", 27017).demo.baesian


@app.post("/item/{itemid:int}")
async def upsert_item(itemid: int, request: Request):
    """Create item"""
    request_params = await request.form()
    if "name" not in request_params:
        return Response(
            "Name not present in parameters!",
            status_code=404,
            media_type="application/json",
        )
    baesian.update_one(
        {"_id": itemid},
        {"$set": {"name": request_params["name"], "nr_votes": 0}},
        upsert=True,
    )

    return Response(
        json.dumps({"_id": itemid, "name": request_params["name"]}),
        status_code=200,
        media_type="application/json",
    )


@app.put("/item/vote/{itemid:int}")
async def add_vote(itemid: int, request: Request):
    """Vote an item"""
    request_params = await request.form()
    if "mark" not in request_params or "userid" not in request_params:
        return Response(
            "Mark and userid must be present in form data!",
            status_code=404,
            media_type="application/json",
        )
    mark = int(request_params["mark"])
    if mark not in range(0, 10):
        return Response(
            "Mark must be in range (0, 10) !",
            status_code=500,
            media_type="application/json",
        )
    userid = int(request_params["userid"])
    update_items_data = {
        "$push": {"marks": {"userid": userid, "mark": mark}},
        "$inc": {"nr_votes": 1, "sum_votes": mark},
    }
    baesian.update_one({"_id": itemid}, update_items_data)
    return Response("", status_code=200, media_type="application/json")


@app.get("/item/{itemid:int}")
async def get_item(itemid: int):
    """Item details"""
    item_data = baesian.find_one({"_id": itemid})
    if None == item_data:
        return Response("", status_code=404, media_type="application/json")
    if "marks" not in item_data:
        item_data["nr_votes"] = 0
        item_data["sum_votes"] = 0
        item_data["baesian_average"] = 0
        return Response(
            json.dumps(item_data), status_code=200, media_type="application/json"
        )

    average_nr_votes_pipeline = [
        {"$group": {"_id": "avg_nr_votes", "avg_nr_votes": {"$avg": "$nr_votes"}}},
    ]
    average_nr_votes = list(baesian.aggregate(average_nr_votes_pipeline))[0][
        "avg_nr_votes"
    ]
    average_rating = [
        {
            "$group": {
                "_id": "avg",
                "avg": {"$sum": "$sum_votes"},
                "count": {"$sum": "$nr_votes"},
            }
        },
        {"$project": {"result": {"$divide": ["$avg", "$count"]}}},
    ]
    average_rating = list(baesian.aggregate(average_rating))[0]["result"]
    item_nr_votes = item_data["nr_votes"]
    item_average_rating = item_data["sum_votes"] / item_data["nr_votes"]
    baesian_average = round(
        ((average_nr_votes * average_rating) + (item_nr_votes * item_average_rating))
        / (average_nr_votes + item_nr_votes),
        3,
    )
    item_data["baesian_average"] = baesian_average
    return Response(
        json.dumps(item_data), status_code=200, media_type="application/json"
    )


@app.get("/items")
async def get_items(request: Request):
    """All items with pagination without averages"""
    request_args = request.query_params
    limit = int(request_args.get("limit")) if "limit" in request_args else 10
    offset = int(request_args.get("offset")) if "offset" in request_args else 0
    item_list = baesian.find().limit(limit).skip(offset)
    if None == baesian:
        return Response(json.dumps([]), status_code=200, media_type="application/json")
    extracted = [
        {
            "_id": d["_id"],
            "name": d["name"],
            "marks": d["marks"] if "marks" in d else [],
        }
        for d in item_list
    ]
    return Response(
        json.dumps(extracted), status_code=200, media_type="application/json"
    )


@app.delete("/item/{itemid:int}")
async def delete_item(itemid: int):
    """Delete operation for a item"""
    baesian.delete_one({"_id": itemid})
    return Response("", status_code=200, media_type="application/json")


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    # starts the app in debug mode, bind on all ip's and on port 5000
    uvicorn.run(app, host="0.0.0.0", port=5000)
