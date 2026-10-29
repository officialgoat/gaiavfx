"""Seed a running app with a movie shot in a set (ADR 0019), through its HTTP API: a place built as the named set, its
angles, a movie with one subject and two scenes, the first opening on the door.

    python tests/browser-set-scenes.py <origin> "<set name>" [<identity picture asset id>]

Prints the movie id. Nothing is rendered.
"""

import sys

import httpx

origin, name = sys.argv[1].rstrip("/"), sys.argv[2]
picture = sys.argv[3] if len(sys.argv) > 3 else None
client = httpx.Client(base_url=origin, timeout=30)
token = client.post(
    "/api/v1/session", headers={"X-GAIAVFX-Bootstrap": "1", "Origin": origin}
).json()["token"]
client.headers["Authorization"] = f"Bearer {token}"


def post(path, body, method="POST"):
    response = client.request(method, path, json=body)
    response.raise_for_status()
    return response.json()


set_id = next(s["id"] for s in client.get("/api/sets").json() if s["name"] == name)
place = next(
    (p for p in client.get("/api/direction/places").json() if p["name"] == name), None
)
place = place or post("/api/direction/places", {"name": name})
known = {
    a["name"]: a
    for a in client.get(f"/api/direction/places/{place['id']}/angles").json()
}
door, stove = (
    known.get(angle)
    or post(f"/api/direction/places/{place['id']}/angles", {"name": angle})
    for angle in ("Door", "Stove")
)
post(
    f"/api/direction/places/{place['id']}/set",
    {"expected_revision": place["revision"], "set_id": set_id},
    method="PUT",
)
movie = post("/api/direction/movies", {"title": f"{name} scenes"})
subject = post(
    f"/api/direction/movies/{movie['id']}/subjects",
    {
        "expected_revision": movie["revision"],
        "name": "Ivo",
        "description": "An old fisher in a wool sweater",
        "keeps": ["the grey wool sweater"],
        "identity_asset_id": picture,
    },
)
revision = subject["receipt"]["after"]["revision"]
segments = []
for description, frames in (
    ("Ivo kneels by the stove and feeds it a log.", 97),
    ("Ivo stands and looks out of the salt-streaked window.", 97),
):
    segment = post(
        f"/api/direction/movies/{movie['id']}/segments",
        {
            "expected_revision": revision,
            "content": {
                "subjects": [subject["id"]],
                "place": {
                    "noun": "shack",
                    "description": "A tiny wooden fishing shack.",
                    "landmarks": "",
                },
            },
            "delivered_frames": frames,
            "continues": False,
            "phases": [
                {
                    "start_frame": 0,
                    "content": {
                        "description": description,
                        "screen_direction": "",
                        "sound": "",
                    },
                }
            ],
        },
    )
    revision = segment["receipt"]["after"]["revision"]
    segments.append(segment)
post(
    f"/api/direction/segments/{segments[0]['id']}",
    {"expected_revision": segments[0]["revision"], "opening_angle_id": door["id"]},
    method="PATCH",
)
print(movie["id"])
