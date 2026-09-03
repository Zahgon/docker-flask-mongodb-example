# Note: the image search algorithm is a naive implementation and it's for demo purposes only
import os
import sys
import io
import json
import shutil
import imagehash
import uvicorn

from PIL import Image, ImageEnhance
from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from http_compat import add_automatic_methods, install_error_pages


app = FastAPI()
install_error_pages(app)
storage_path = "/root/storage" if len(sys.argv) == 1 else sys.argv[1]


class FileHashSearch:
    hashes = {}

    def load_from_path(self, path: str) -> None:
        for root, subdirs, files in os.walk(path):
            for file in os.listdir(root):
                filePath = os.path.join(root, file)
                hash = imagehash.average_hash(Image.open(filePath))
                self.hashes[hash] = os.path.splitext(file)[0]

    def add(self, file, id) -> None:
        self.hashes[imagehash.average_hash(Image.open(file.file))] = id

    def delete(self, id: int) -> None:
        self.hashes = {k: v for k, v in self.hashes.items() if v != str(id)}

    def get_similar(self, hash, similarity: int = 10):
        return [
            self.hashes[current_hash]
            for id, current_hash in enumerate(self.hashes)
            if hash - current_hash < similarity
        ]


def get_photo_path(photo_id: str):
    return "{0}/{1}.jpg".format(storage_path, str(photo_id))


def get_resized_by_height(img, new_height: int):
    width, height = img.size
    hpercent = new_height / float(height)
    wsize = int((float(width) * float(hpercent)))
    return img.resize((wsize, new_height), Image.ANTIALIAS)


file_hash_search = FileHashSearch()
file_hash_search.load_from_path(storage_path)


# ``{id:int}`` is Starlette's equivalent of Flask's ``<int:id>``: it matches
# digits only, so ``/photo/similar`` never binds here.  A bare ``{id}`` matches
# any segment, which let this route shadow the literal ``/photo/similar`` rule
# and made declaration order load-bearing.
@app.get("/photo/{id:int}")
async def get_photo(id: int, request: Request):
    """Returns the photo by id"""
    request_args = request.query_params
    resize = int(request_args.get("resize")) if "resize" in request_args else 0
    rotate = int(request_args.get("rotate")) if "rotate" in request_args else 0
    brightness = (
        float(request_args.get("brightness")) if "brightness" in request_args else 0
    )
    if brightness > 20:
        return get_response({"error": "Maximum value for brightness is 20"}, 500)

    try:
        img = Image.open(get_photo_path(id))
    except IOError:
        return get_response({"error": "Error loading image"}, 500)

    if resize > 0:
        img = get_resized_by_height(img, resize)
    if rotate > 0:
        img = img.rotate(rotate)
    if brightness > 0:
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(brightness)
    output = io.BytesIO()
    img.save(output, format="JPEG")
    image_data = output.getvalue()
    output.close()
    return Response(image_data, status_code=200, media_type="image/jpeg")


@app.put("/photo/similar")
async def get_photos_like_this(request: Request):
    """Find similar photos"""
    file = (await request.form()).get("file")
    if not isinstance(file, UploadFile):
        return get_response({"error": "File parameter not present!"}, 500)
    if file.content_type != "image/jpeg":
        return get_response({"error": "File mimetype must pe jpeg!"}, 500)

    request_args = request.query_params
    similarity = (
        int(request_args.get("similarity")) if "similarity" in request_args else 10
    )
    result = file_hash_search.get_similar(
        imagehash.average_hash(Image.open(file.file)), similarity
    )

    return Response(json.dumps(result), status_code=200, media_type="application/json")


@app.put("/photo/{id:int}")
async def set_photo(id: int, request: Request):
    """Add jpeg photo on disk"""
    file = (await request.form()).get("file")
    if not isinstance(file, UploadFile):
        return get_response({"error": "File parameter not present!"}, 500)

    if file.content_type != "image/jpeg":
        return get_response({"error": "File mimetype must pe jpeg!"}, 500)

    try:
        with open(get_photo_path(id), "wb") as destination:
            shutil.copyfileobj(file.file, destination)
    except Exception as e:
        return get_response({"error": "Could not save file to disk!"}, 500)

    file_hash_search.add(file, id)
    return get_response({"status": "success"}, 200)


@app.delete("/photo/{id:int}")
async def delete_photo(id: int):
    """Delete photo by id"""
    try:
        os.remove(get_photo_path(id))
        file_hash_search.delete(id)
    except OSError as e:
        return get_response({"error": "File does not exists!"}, 500)

    return get_response({"status": "success"}, 200)


def get_response(data: dict, status: int) -> Response:
    return Response(
        json.dumps(data),
        status_code=status,
        media_type="application/json",
    )


# Werkzeug served HEAD and OPTIONS on every rule automatically; restore that
# now that all the routes above are registered.
add_automatic_methods(app)


if __name__ == "__main__":
    # starts the app in debug mode, bind on all ip's and on port 5000
    uvicorn.run(app, host="0.0.0.0", port=5000)
