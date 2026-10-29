"""Inference-graph comparison shared by the H3 fixture parity tests."""

import json
from collections.abc import Mapping
from copy import deepcopy

PASSTHROUGH = {"GaiaScreenFreshModel": "model", "GaiaCampaignModelProbe": "model"}
SINKS = {"GaiaScreenSaveDecoded", "PreviewAny"}
IGNORED = {"filename_prefix", "run_id", "label", "name"}


def node_of(graph, kind):
    return next(n for n in graph.values() if n["class_type"] == kind)


def signature(graph):
    """The graph's inference structure: every node by class and literal inputs, references resolved recursively."""

    def resolve(ref):
        key, output = ref
        while graph[key]["class_type"] in PASSTHROUGH:
            key, output = graph[key]["inputs"][PASSTHROUGH[graph[key]["class_type"]]]
        return key, output

    def canon(key):
        node, items = graph[key], []
        for name, value in sorted(node["inputs"].items()):
            if name in IGNORED:
                continue
            if isinstance(value, list) and len(value) == 2 and value[0] in graph:
                target, output = resolve(value)
                items.append((name, "ref", canon(target), output))
            else:
                items.append((name, "lit", json.dumps(value)))
        return (node["class_type"], tuple(items))

    return sorted(
        repr(canon(key))
        for key, node in graph.items()
        if node["class_type"] not in SINKS | set(PASSTHROUGH)
    )


def content_signature(graph, sha_by_name: Mapping[str, str]):
    normalized = deepcopy(graph)
    for node in normalized.values():
        for name, value in node["inputs"].items():
            if (node["class_type"] == "LoadImage" and name == "image") or name == "latent_path":
                node["inputs"][name] = sha_by_name[value]
    return signature(normalized)
