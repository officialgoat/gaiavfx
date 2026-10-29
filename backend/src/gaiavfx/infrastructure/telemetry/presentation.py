"""Stable operations remain metadata; observation names are written for people."""

NAMES = {
    "task.assistant": "AI assistance",
    "task.agent": "Author with agent",
    "agent.run": "Agent session",
    "agent.turn": "Agent turn",
    "task.generation": "Render video",
    "task.import": "Import media",
    "task.extract": "Extract frame",
    "task.export": "Export movie",
    "task.join": "Render join",
    "task.assemble": "Assemble sequence",
    "task.quality": "Measure take",
    "task.filmstrip": "Build filmstrip",
    "task.transition": "Job status",
    "assistant.workflow": "Resolve the requested assistance",
    "assistant.resource": "Prepare model and run request",
    "assistant.local": "Local assistant",
    "assistant.remote": "Remote assistant",
    "assistant.budget": "Check context and token allowance",
    "assistant.parse": "Parse the model answer",
    "assistant.request": "Queue AI assistance",
    "grounding.manifest": "Resolve reference sources",
    "grounding.observe": "Observe reference media",
    "grounding.batch": "Inspect reference batch",
    "grounding.cache_hit": "Reuse recorded observations",
    "grounding.reconcile": "Reconcile subjects and references",
    "inputs.prepare": "Prepare model inputs",
    "inputs.resolve_assets": "Resolve source assets",
    "prompt.resolve_inputs": "Resolve prompt inputs",
    "prompt.compile": "Compile final prompt",
    "prompt.manual": "Draft final prompt",
    "prompt.accept": "Accept final prompt",
    "prompt.request": "Request final wording",
    "prompt.write": "Write final prompt",
    "prompt.directed_writing": "Write directed phases",
    "prompt.fidelity": "Review writing fidelity",
    "generation.prepare_accepted": "Check accepted prompt",
    "generation.prepare_frozen": "Freeze generation inputs",
    "generation.submit_frozen": "Queue frozen generation",
    "generation.render": "Generate candidate footage",
    "generation.collect": "Collect generated outputs",
    "generation.reconcile": "Reconcile remote submission",
    "comfy.prompt": "Submit workflow to ComfyUI",
    "comfy.free": "Release cached GPU weights",
    "comfy.capabilities": "Check installed models and nodes",
    "comfy.validate_graph": "Validate ComfyUI workflow",
    "comfy.upload": "Upload prepared input",
    "comfy.download": "Download generated artifact",
    "resource.wait_endpoint": "Wait for local inference resources",
    "media.run": "Run media processor",
    "media.probe": "Inspect media properties",
    "media.prepare_reference": "Prepare reference media",
    "media.prepare_continuation": "Prepare continuation media",
    "media.sample_frame": "Sample video frame",
    "media.frame": "Extract exact frame",
    "media.ending_frame": "Extract ending frame",
    "media.thumbnail": "Build thumbnail",
    "media.preview": "Build playback preview",
    "media.import": "Import source media",
    "media.compose_audio": "Compose soundtrack",
    "media.export_sequence": "Encode movie",
    "media.join": "Render join preview",
    "movie.export": "Export movie",
    "take.measure": "Measure generated take",
}


def title(operation, metadata=None):
    if operation in NAMES:
        return NAMES[operation]
    if operation.startswith("command."):
        name = operation.removeprefix("command.")
        suffixes = {
            "final-prompt.manual": "Draft final prompt",
            "final-prompt.accept": "Accept final prompt",
            "final-prompt.generate": "Request final wording",
            "generation-reviews.confirm": "Start reviewed render",
            "generation-reviews.validate": "Validate render settings",
            "generation-reviews": "Prepare render review",
            "proposals.apply": "Apply AI proposal",
            "proposals.native-apply": "Apply AI track changes",
            "proposals": "Request AI assistance",
            "open-movie": "Open recipe as movie",
            "tracks": "Save movie tracks",
            "imports": "Import media",
            "recipes": "Save recipe",
            "exports": "Export movie",
        }
        for suffix, label in suffixes.items():
            if name.endswith(suffix):
                return label
    return operation.replace("command.", "").replace(".", " · ").replace("_", " ").capitalize()


def workflow_title(operation, payload):
    verb = title(operation)
    if operation == "task.assistant":
        verb = {
            "describe": "Describe references",
            "refine": "Refine direction",
            "final_prompt": "Write final prompt",
            "plan": "Plan movie",
        }.get(payload.get("kind"), verb)
    name = payload.get("name") or (payload.get("recipe") or {}).get("name")
    return f"{verb} · {name}" if name else verb


def model_title(model, phase):
    lower = model.lower()
    family = (
        "Qwen"
        if "qwen" in lower
        else "Gemma"
        if "gemma" in lower
        else model.rsplit("/", 1)[-1][:64]
    )
    return family + (" · Observe references" if phase == "observation" else " · Write response")
