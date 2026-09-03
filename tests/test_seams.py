import os
import pytest
import requests
from pathlib import Path
from requests.auth import HTTPBasicAuth
from typing import Generator
from utils import Collection

baesian_host = "http://web-baesian:5000"
bookcollection_host = "http://web-book-collection:5000"
fulltext_search_host = "http://web-fulltext-search:5000"
geolocation_host = "http://web-geolocation-search:5000"
graphql_host = "http://web-users-graphql:5000"
photo_process_host = "http://web-photo-process:5000"
random_host = "http://web-random:5000"
tictactoe_host = "http://web-tictactoe:5000"
users_host = "http://web-users:5000"

parent_path = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
image_path = os.path.join(str(parent_path) + "/tests/resources/test.jpg")
seam_user_id = 9200
seam_photo_id = 9202


@pytest.fixture
def seam_user(demo_db) -> Generator[dict, None, None]:
    payload = {
        "email": "seam@example.com",
        "name": "Seam",
        "birthdate": "1980-01-01",
        "country": "Romania",
    }
    requests.post(url="{0}/users/{1}".format(users_host, seam_user_id), data=payload)
    yield payload
    Collection(demo_db, "users").delete(seam_user_id)


def test_fulltext_search_rejects_missing_credentials():
    response = requests.get(url="{0}/search/anything".format(fulltext_search_host))
    assert response.status_code == 401
    assert response.text == "Unauthorized Access"
    assert response.headers["Content-Type"].startswith("text/html")
    assert (
        response.headers["WWW-Authenticate"] == 'Basic realm="Authentication Required"'
    )


def test_fulltext_search_rejects_wrong_credentials():
    response = requests.get(
        url="{0}/search/anything".format(fulltext_search_host),
        auth=HTTPBasicAuth("admin", "wrong"),
    )
    assert response.status_code == 401
    assert response.text == "Unauthorized Access"


def test_geolocation_rejects_missing_authorization_header():
    response = requests.get(url="{0}/location/40.7/-74.0".format(geolocation_host))
    assert response.status_code == 401
    assert response.json() == {"msg": "Missing Authorization Header"}


def test_geolocation_rejects_non_bearer_authorization():
    response = requests.get(
        url="{0}/location/40.7/-74.0".format(geolocation_host),
        headers={"Authorization": "Basic abcdef"},
    )
    assert response.status_code == 401
    assert response.json() == {
        "msg": "Missing 'Bearer' type in 'Authorization' header. Expected 'Authorization: Bearer <JWT>'"
    }


def test_geolocation_rejects_malformed_bearer_token():
    response = requests.get(
        url="{0}/location/40.7/-74.0".format(geolocation_host),
        headers={"Authorization": "Bearer garbage"},
    )
    assert response.status_code == 422
    assert response.json() == {"msg": "Not enough segments"}


def test_random_generator_inverted_defaults():
    response = requests.get(url="{0}/random".format(random_host))
    assert response.status_code == 400
    assert response.json() == {
        "error": "Upper boundary must be greater or equal than lower boundary"
    }


def test_baesian_rejects_mark_outside_range():
    response = requests.put(
        url="{0}/item/vote/9201".format(baesian_host),
        data={"mark": 10, "userid": 1},
    )
    assert response.status_code == 500
    assert response.text == "Mark must be in range (0, 10) !"


def test_photo_rejects_non_jpeg_upload():
    response = requests.put(
        url="{0}/photo/{1}".format(photo_process_host, seam_photo_id),
        files={"file": ("test.txt", b"not a photo", "text/plain")},
    )
    assert response.status_code == 500
    assert response.json() == {"error": "File mimetype must pe jpeg!"}


def test_photo_similar_is_not_shadowed_by_the_id_route():
    response = requests.put(
        url="{0}/photo/similar".format(photo_process_host),
        files={"file": ("test.jpg", open(image_path, "rb"), "image/jpeg")},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_bookcollection_rejects_invalid_pagination():
    response = requests.get(
        url="{0}/book?limit=abc&offset=0".format(bookcollection_host)
    )
    assert response.status_code == 400
    assert response.json() == {
        "errors": {"limit": "Limit invalid literal for int() with base 10: 'abc'"},
        "message": "Input payload validation failed",
    }


def test_tictactoe_session_tracks_moves():
    session = requests.Session()
    board = session.get(url="{0}/".format(tictactoe_host))
    assert board.status_code == 200
    assert board.headers["Content-Type"].startswith("text/html")
    assert board.text.count("Play X here.") == 9

    move = session.get(url="{0}/play/0/0".format(tictactoe_host), allow_redirects=False)
    assert move.status_code == 302

    board = session.get(url="{0}/".format(tictactoe_host))
    assert board.text.count("Play O here.") == 8
    assert board.text.count("Play X here.") == 0


def test_tictactoe_reset_clears_the_board():
    session = requests.Session()
    session.get(url="{0}/".format(tictactoe_host))
    session.get(url="{0}/play/1/1".format(tictactoe_host))

    reset = session.get(url="{0}/reset".format(tictactoe_host), allow_redirects=False)
    assert reset.status_code == 302

    board = session.get(url="{0}/".format(tictactoe_host))
    assert board.text.count("Play X here.") == 9


def test_graphql_playground_is_served():
    response = requests.get(url="{0}/graphql".format(graphql_host))
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert "GraphQL" in response.text


def test_graphql_validation_error_returns_400():
    response = requests.post(
        url="{0}/graphql".format(graphql_host), json={"query": "{ nope }"}
    )
    assert response.status_code == 400
    assert (
        "Cannot query field 'nope' on type 'Query'."
        in response.json()["errors"][0]["message"]
    )


def test_users_cached_read_is_stable(seam_user):
    url = "{0}/users/{1}".format(users_host, seam_user_id)
    first = requests.get(url=url)
    second = requests.get(url=url)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["email"] == seam_user["email"]
