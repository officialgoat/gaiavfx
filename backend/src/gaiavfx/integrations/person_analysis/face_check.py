"""One analysis graph per take; separate target/mask bindings for each performer."""


def build_face_check_graph(
    candidate_file,
    source_file,
    performers,
    models_root="",
    *,
    min_face_height_fraction=0.0,
    min_face_frames_fraction=0.0,
):
    graph = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    def video(key, file):
        loaded = add(key + "_video", "LoadVideo", file=file)
        return add(key, "GetVideoComponents", video=loaded)

    candidate, source = video("candidate", candidate_file), video("source", source_file)
    for i, performer in enumerate(performers):
        prefix = f"performer_{i}"
        mask = video(prefix + "_mask", performer["mask_file"])
        mask = add(prefix + "_mask_channel", "ImageToMask", image=mask, channel="red")
        target = add(prefix + "_target", "LoadImage", image=performer["target_file"])
        add(
            prefix,
            "GaiaFaceCheck",
            candidate=candidate,
            source=source,
            target=target,
            performer=mask,
            models_root=models_root,
            min_face_height_fraction=min_face_height_fraction,
            min_face_frames_fraction=min_face_frames_fraction,
        )
    return graph
