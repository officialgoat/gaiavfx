"""Remote path names do not change inference graph parity."""

import json
from copy import deepcopy
from pathlib import Path

from parity_graph import content_signature


def test_content_signature_uses_file_hashes():
    fixture = json.loads((Path(__file__).parent / "fixtures/parity/DZ.json").read_text())
    graph = fixture["graph"]
    sha_by_name = {
        node["inputs"][item["input"]]: item["sha256"]
        for item in fixture["files"]
        for node in [graph[item["node"]]]
    }
    renamed = deepcopy(graph)
    changed_names = dict(sha_by_name)
    for node in renamed.values():
        for field, value in node["inputs"].items():
            if (node["class_type"] == "LoadImage" and field == "image") or field == "latent_path":
                new_name = "renamed/" + value
                changed_names[new_name] = sha_by_name[value]
                node["inputs"][field] = new_name
    assert content_signature(graph, sha_by_name) == content_signature(renamed, changed_names)
    changed_names[next(name for name in changed_names if name.startswith("renamed/"))] = "different"
    assert content_signature(graph, sha_by_name) != content_signature(renamed, changed_names)
