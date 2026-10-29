"""ADR 0010 decision 11: enforce every dependency direction, including relative imports."""

import ast
import re
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "src" / "gaiavfx"


def imported_names(file):
    """Resolve imported modules, including `from .. import module` and aliases."""
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if not file.is_relative_to(SOURCE):
                    continue
                parent = ("gaiavfx", *file.relative_to(SOURCE).parts[:-1])
                base = ".".join((*parent[: len(parent) - node.level + 1], *base.split("."))).rstrip(
                    "."
                )
            for alias in node.names:
                candidate = base + "." + alias.name
                local = SOURCE.parent.joinpath(*candidate.split("."))
                yield candidate if local.is_dir() or local.with_suffix(".py").is_file() else base


def imports_in(folder):
    for file in (SOURCE / folder).rglob("*.py"):
        for name in imported_names(file):
            yield file, name


def test_module_and_domain_boundaries():
    violations = []
    for file, name in imports_in("modules"):
        parts = file.relative_to(SOURCE).parts
        forbidden = name.startswith(("gaiavfx.integrations", "gaiavfx.api", "gaiavfx.application"))
        if name.startswith("gaiavfx.modules."):
            imported = name.split(".")
            forbidden |= imported[2] != parts[1] and not name.endswith((".application", ".service"))
        if "domain" in parts or file.stem == "domain":
            forbidden |= name.split(".")[0] in {
                "fastapi",
                "sqlalchemy",
                "pydantic",
                "httpx",
                "requests",
                "os",
                "pathlib",
            }
            forbidden |= (
                name.startswith(
                    (
                        "gaiavfx.infrastructure",
                        "gaiavfx.application",
                        "gaiavfx.api",
                        "gaiavfx.integrations",
                    )
                )
                or ".persistence" in name
            )
        if forbidden:
            violations.append(f"{file.relative_to(SOURCE)}: imports {name}")
    assert not violations, "\n".join(violations)


def test_integration_boundaries():
    allowed = {
        "gaiavfx.shared.errors",
        *(
            "gaiavfx.modules.direction." + name
            for name in (
                "capabilities",
                "segment",
                "survey",
                "timebase",
                "techniques",
                "insertion",
                "portrait",
                "replacement",
            )
        ),
        # A fill probe's model-free request (ADR 0020 §6).
        "gaiavfx.modules.sets.probe",
    }
    violations = []
    for file, name in imports_in("integrations"):
        package = ".".join(("gaiavfx", *file.relative_to(SOURCE).parts[:2]))
        if (
            name.startswith("gaiavfx")
            and name not in allowed
            and name != package
            and not name.startswith(package + ".")
        ):
            violations.append(f"{file.relative_to(SOURCE)}: imports {name}")
    assert not violations, "\n".join(violations)


def test_infrastructure_boundaries():
    violations = [
        f"{file.relative_to(SOURCE)}: imports {name}"
        for file, name in imports_in("infrastructure")
        if name.startswith(
            (
                "gaiavfx.integrations",
                "gaiavfx.modules",
                "gaiavfx.application",
                "gaiavfx.api",
            )
        )
    ]
    assert not violations, "\n".join(violations)


def test_shared_boundaries_and_model_neutrality():
    violations = [
        f"{file.relative_to(SOURCE)}: imports {name}"
        for file, name in imports_in("shared")
        if name.startswith("gaiavfx") and not name.startswith("gaiavfx.shared.")
    ]
    for file in (SOURCE / "shared").rglob("*.py"):
        if re.search(
            r"\b(h3|minimax|ltx|klein|qwen)\b", file.read_text(encoding="utf-8"), re.IGNORECASE
        ):
            violations.append(f"{file.relative_to(SOURCE)}: contains model-specific text")
    assert not violations, "\n".join(violations)


def test_application_boundaries():
    violations = [
        f"{file.relative_to(SOURCE)}: imports {name}"
        for file, name in imports_in("application")
        if name.startswith("gaiavfx.api")
    ]
    assert not violations, "\n".join(violations)


def test_router_boundaries():
    violations = []
    for file, name in imports_in("api"):
        if file.name != "app.py" and ".persistence" in name:
            violations.append(f"{file.name}: imports persistence {name}")
        if file.name not in {
            "app.py",
            "telemetry.py",
            "assistant_connections.py",
        } and name.startswith(("gaiavfx.infrastructure", "gaiavfx.integrations")):
            violations.append(f"{file.name}: imports {name}")
    assert not violations, "\n".join(violations)


def test_telemetry_vendors_stay_in_infrastructure():
    violations = [
        f"{file.relative_to(SOURCE)}: imports {name}"
        for file in SOURCE.rglob("*.py")
        for name in imported_names(file)
        if file.relative_to(SOURCE).parts[0] != "infrastructure"
        and name.split(".")[0] in {"langfuse", "opentelemetry"}
    ]
    assert not violations, "\n".join(violations)


def test_comfy_nodes_never_import_the_application():
    nodes = Path(__file__).parents[2] / "comfy_nodes"
    violations = [
        f"{file.relative_to(nodes)}: imports {name}"
        for file in nodes.rglob("*.py")
        for name in imported_names(file)
        if name.split(".")[0] == "gaiavfx"
    ]
    assert list(nodes.glob("*/__init__.py")), "comfy_nodes packages not found"
    assert not violations, "\n".join(violations)


WORKER_STDLIB = {
    "__future__",
    "argparse",
    "contextlib",
    "json",
    "os",
    "pathlib",
    "sys",
    "traceback",
}


def test_engine_workers_never_import_the_application():
    """ADR 0015: workers run in their own environments and speak a file protocol."""
    workers = Path(__file__).parents[2] / "engine_workers"
    assert (workers / "protocol.py").is_file()
    violations = [
        f"{file.relative_to(workers)}: imports {name}"
        for file in workers.rglob("*.py")
        for name in imported_names(file)
        if name.split(".")[0] == "gaiavfx"
    ]
    assert not violations, "\n".join(violations)
    protocol = set(imported_names(workers / "protocol.py"))
    assert {name.split(".")[0] for name in protocol} <= WORKER_STDLIB, protocol


def test_the_engine_probe_needs_only_the_standard_library():
    """The probe runs inside every engine environment before anything is known about it; torch is optional."""
    tree = ast.parse(
        (Path(__file__).parents[2] / "engine_workers" / "probe.py").read_text(encoding="utf-8")
    )
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    assert top <= WORKER_STDLIB | {"importlib", "platform"}, top


def test_voice_integration_boundaries():
    folder = SOURCE / "integrations" / "voice"
    assert folder.is_dir()
    for file in folder.rglob("*.py"):
        for name in imported_names(file):
            assert name.split(".")[0] in {
                "copy",
                "dataclasses",
                "hashlib",
                "importlib",
                "os",  # expressive.scan lists the expressive library with os.scandir
                "pathlib",
                "re",
                "tomllib",
            } or name.startswith("gaiavfx.integrations.voice"), f"{file}: {name}"


def test_face_metrics_import_only_numpy():
    file = Path(__file__).parents[2] / "comfy_nodes" / "gaiavfx" / "metrics.py"
    assert set(imported_names(file)) == {"numpy"}


DIRECTION_STDLIB = {
    "statistics",
    "__future__",
    "collections",
    "copy",
    "datetime",
    "dataclasses",
    "enum",
    "fractions",
    "functools",
    "importlib",
    "math",
    "re",
    "tomllib",
    "typing",
}


def test_direction_core_is_model_free_and_pure():
    """ADR 0010: pure planning files and Direction storage keep their distinct dependencies."""
    violations = []
    for file in (SOURCE / "modules" / "direction").rglob("*.py"):
        relative = file.relative_to(SOURCE)
        for name in imported_names(file):
            top = name.split(".")[0]
            if file.name in {"service.py", "persistence.py"} and (
                top == "sqlalchemy"
                or name in {"gaiavfx.infrastructure.database", "gaiavfx.shared.errors"}
            ):
                continue
            if top == "gaiavfx":
                if not name.startswith("gaiavfx.modules.direction"):
                    violations.append(f"{relative}: imports {name}")
            elif top not in DIRECTION_STDLIB:
                violations.append(f"{relative}: imports {name}")
            elif top == "importlib" and file.name != "application.py":
                violations.append(f"{relative}: reads package files outside application.py")
    for name in (
        "coverage",
        "techniques",
        "capabilities",
        "planning",
        "timebase",
        "segment",
        "content",
        "place_text",
        "insertion",
        "portrait",
        "replacement",
        "person_crop",
        "film",
        "mouth",
        "head",
        "target_face",
        "dialogue",
    ):
        file = SOURCE / "modules" / "direction" / f"{name}.py"
        assert all(
            imported.split(".")[0] in DIRECTION_STDLIB
            or imported.startswith("gaiavfx.modules.direction.")
            for imported in imported_names(file)
        ), f"{file.name} must use only the standard library and Direction"
    assert all(
        name.split(".")[0] in DIRECTION_STDLIB or name == "gaiavfx.modules.direction.segment"
        for name in imported_names(SOURCE / "modules" / "direction" / "content.py")
    )
    for file in (SOURCE / "integrations" / "minimax_h3").rglob("*.py"):
        relative = file.relative_to(SOURCE)
        for name in imported_names(file):
            if name.split(".")[0] not in sys.stdlib_module_names | {"gaiavfx"}:
                violations.append(f"{relative}: imports non-standard-library dependency {name}")
            if name.startswith("gaiavfx.") and not name.startswith(
                (
                    "gaiavfx.modules.direction.capabilities",
                    "gaiavfx.modules.direction.segment",
                    "gaiavfx.modules.direction.survey",
                    "gaiavfx.modules.direction.replacement",
                    "gaiavfx.modules.direction.timebase",
                    "gaiavfx.modules.direction.techniques",
                    # A fill probe's model-free request (ADR 0018 §4, ADR 0020 §6), as a survey's is Direction's.
                    "gaiavfx.modules.sets.probe",
                    "gaiavfx.integrations.minimax_h3",
                )
            ):
                violations.append(f"{relative}: imports {name}")
    probe = SOURCE / "modules" / "sets" / "probe.py"
    for name in imported_names(probe):
        if name.split(".")[0] not in sys.stdlib_module_names:
            violations.append(
                f"{probe.relative_to(SOURCE)}: a probe request imports only the standard library"
            )
    assert not violations, "\n".join(violations)


def test_qwen_image_edit_boundaries():
    """The image integration reads Direction requests, never persistence or another integration."""
    violations = []
    insertion = SOURCE / "modules" / "direction" / "insertion.py"
    for name in imported_names(insertion):
        if name.split(".")[0] not in {"dataclasses", "math", "re", "typing"}:
            violations.append(f"{insertion.relative_to(SOURCE)}: imports {name}")
    for file in (SOURCE / "integrations" / "qwen_image_edit").rglob("*.py"):
        for name in imported_names(file):
            top = name.split(".")[0]
            if (
                top == "gaiavfx"
                and not name.startswith(
                    (
                        "gaiavfx.integrations.qwen_image_edit",
                        "gaiavfx.modules.direction.insertion",
                        "gaiavfx.modules.direction.portrait",
                    )
                )
            ) or top not in {
                "gaiavfx",
                "tomllib",
                "collections",
                "dataclasses",
                "functools",
                "importlib",
            }:
                violations.append(f"{file.relative_to(SOURCE)}: imports {name}")
    assert not violations, "\n".join(violations)


def test_generation_operations_store_boundaries():
    """The Generation store depends on no integration, API or other module internals."""
    store = SOURCE / "modules" / "generation" / "operations"
    allowed = {
        "gaiavfx.infrastructure.database",
        "gaiavfx.shared.errors",
    }
    violations = []
    for file in store.glob("*.py"):
        if file.name == "prescreen.py":
            assert all(
                name.split(".")[0] in {"__future__", "copy", "typing"}
                for name in imported_names(file)
            )
        for name in imported_names(file):
            top = name.split(".")[0]
            if file.name == "lifecycle.py":
                if top not in {"dataclasses", "typing", "__future__"}:
                    violations.append(f"{file.name}: lifecycle imports {name}")
            elif (
                top == "gaiavfx"
                and not (
                    name in allowed or name.startswith("gaiavfx.modules.generation.operations")
                )
            ) or top not in {
                "gaiavfx",
                "sqlalchemy",
                "copy",
                "typing",
                "__future__",
                "random",
                "datetime",
                "json",
                "tomllib",
                "functools",
                "importlib",
            }:
                violations.append(f"{file.name}: store imports {name}")
    assert not violations, "\n".join(violations)


def test_take_selection_store_boundaries():
    """Editing selection reads no Generation tables or other module internals."""
    store = SOURCE / "modules" / "editing" / "selections"
    violations = []
    for file in store.glob("*.py"):
        if file.name == "prescreen.py":
            assert all(
                name.split(".")[0] in {"__future__", "copy", "typing"}
                for name in imported_names(file)
            )
        for name in imported_names(file):
            top = name.split(".")[0]
            if (
                top == "gaiavfx"
                and not (
                    name in {"gaiavfx.infrastructure.database", "gaiavfx.shared.errors"}
                    or name.startswith("gaiavfx.modules.editing.selections")
                )
                or top not in {"gaiavfx", "sqlalchemy", "__future__"}
            ):
                violations.append(f"{file.name}: imports {name}")
    assert not violations, "\n".join(violations)


def test_persisted_render_and_import_use_public_module_services():
    files = [
        SOURCE / "application" / "authored.py",
        SOURCE / "application" / "render_cut.py",
        SOURCE / "application" / "export_movie.py",
        SOURCE / "application" / "recast_conform.py",
        SOURCE / "application" / "film_analysis.py",
        SOURCE / "application" / "recast_steps.py",
        SOURCE / "application" / "dialogue_analysis.py",
        SOURCE / "application" / "dialogue_steps.py",
        SOURCE / "application" / "recast_lines.py",
    ]
    violations = [
        f"{file.name}: imports {name}"
        for file in files
        for name in imported_names(file)
        if name.startswith(("sqlalchemy", "gaiavfx.api")) or ".persistence" in name
    ]
    assert not violations, violations


def test_agent_store_boundaries():
    violations = []
    for file in (SOURCE / "modules" / "agent").glob("*.py"):
        for name in imported_names(file):
            top = name.split(".")[0]
            if top in sys.stdlib_module_names | {"sqlalchemy"}:
                continue
            if name in {
                "gaiavfx.infrastructure.database",
                "gaiavfx.shared.errors",
            } or name.startswith("gaiavfx.modules.agent"):
                continue
            violations.append(f"{file.name}: imports {name}")
    assert not violations, violations


def test_harness_boundaries_and_human_authority():
    forbidden = (
        "gaiavfx.api",
        "gaiavfx.integrations",
        "gaiavfx.application.workspace",
    )
    human = {
        "confirm_appearance",
        "reject_appearance",
        "clear_appearance",
        "_decide_appearance",
        "clear_target",
        "_target",
        "choose_version",
        "start_head_pass",
        "confirm_target",
        "approve_reference",
        "reject_reference",
        "set_target",
        "start_conform",
        "run_conform",
        "approve_view",
        "reject_reference_review",
        "reject_references",
        "reference_approved",
        "approve_pin",
        "approve_plate",
        "approve_replacement",
        "approve_plate_replacement",
        "_replace_plate",
        "set_plate",
        "judge_suitability",
        "approve_evidence",
        "approve_keyframe",
        "approve_keyframe_replacement",
        "_approve_keyframe",
        "_replace_keyframe",
        "save_ranking",
        "reveal",
        "undo",
        "undo_edit",
        # ADR 0016 §8: the dialogue analysis is asked for, and line decisions are the user's.
        "start_dialogue_analysis",
        "retry_dialogue_analysis",
        "confirm_line",
        "reject_line",
        "clear_line",
        "edge_line",
        "confirm_lines",
        # ADR 0016 §4, §8 (P5): voice recordings, the build, the audition's approval are the user's.
        "set_voice",
        "clear_voice",
        "confirm_voice",
        "build_voice",
        "retry_voice",
        "approve_voice",
        "reject_voice",
        # ADR 0018 §5, ADR 0019 §7: a set's versions are accepted or discarded by the user alone.
        "accept_version",
        "discard_version",
    }
    violations = []
    for file in (SOURCE / "application" / "harness").rglob("*.py"):
        restricted = human | (
            {"approve_plan", "revise_plan", "reject_plan", "set_plan_mode", "resume_reviewed"}
            if "tools" in file.parts
            else set()
        )
        source = file.read_text("utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [
                    node.module or "",
                    *[f"{node.module}.{alias.name}" for alias in node.names],
                ]
                if restricted & {alias.name for alias in node.names}:
                    violations.append(f"{file.name}: imports a human command")
                if node.level:
                    parent = file.relative_to(SOURCE).parts[: -node.level]
                    names += ["gaiavfx." + ".".join((*parent, node.module or ""))]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                names = []
            for name in names:
                if name.startswith(forbidden) or ".persistence" in name:
                    violations.append(f"{file.name}: imports {name}")
            if isinstance(node, ast.Call):
                called = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", "")
                )
                if called in restricted or called in {"JobSpec"}:
                    violations.append(f"{file.name}: calls {called}")
                if any(
                    k.arg == "authority"
                    and isinstance(k.value, ast.Constant)
                    and k.value.value == "user"
                    for k in node.keywords
                ):
                    violations.append(f"{file.name}: selects as user")
        assert 'authority="user"' not in source
        assert not any(
            isinstance(node, ast.keyword)
            and node.arg == "basis"
            and isinstance(node.value, ast.Constant)
            and node.value.value == "prescreen"
            for node in ast.walk(tree)
        ), f"{file.name}: selects from a prescreen"
    assert not violations, violations
    picks = SOURCE / "application" / "harness" / "tools" / "picks.py"
    assert "prescreen" not in picks.read_text("utf-8")


def test_agent_api_uses_only_public_harness_interfaces():
    allowed = (
        "gaiavfx.application.harness",
        "gaiavfx.modules.agent.service",
        "gaiavfx.shared",
    )
    for name in imported_names(SOURCE / "api" / "agent.py"):
        assert name.split(".")[0] in sys.stdlib_module_names | {
            "pydantic",
            "fastapi",
        } or name.startswith(allowed), name


def test_harness_units_bounded_and_knowledge_has_one_loader():
    root = SOURCE / "application" / "harness"
    for path in root.rglob("*.py"):
        source = path.read_text("utf-8")
        assert len(source.splitlines()) <= 600, path
        if path != root / "knowledge" / "__init__.py":
            assert "tomllib" not in source, path


def test_person_analysis_integration_boundaries():
    folder = SOURCE / "integrations" / "person_analysis"
    assert folder.is_dir()
    for file in folder.rglob("*.py"):
        for name in imported_names(file):
            assert name.split(".")[0] in {"json", "tomllib", "importlib"} or name.startswith(
                "gaiavfx.integrations.person_analysis"
            ), f"{file}: {name}"


def test_recast_store_boundaries():
    for file in (SOURCE / "modules" / "editing" / "recast").glob("*.py"):
        for name in imported_names(file):
            assert name.split(".")[0] in sys.stdlib_module_names | {"sqlalchemy"} or (
                name in {"gaiavfx.infrastructure.database", "gaiavfx.shared.errors"}
                or name.startswith("gaiavfx.modules.editing.recast")
            ), f"{file}: {name}"


def test_target_face_reports_do_not_depend_on_recasts():
    imports = imported_names(SOURCE / "application" / "target_faces.py")
    assert not any(name.startswith("gaiavfx.application.recast") for name in imports)


def test_dialogue_analysis_does_not_depend_on_recasts():
    """ADR 0016 §2: the shared dialogue analysis is Library evidence; it knows no recast and no Editing table."""
    for file in ("dialogue_analysis.py", "dialogue_steps.py"):
        imports = imported_names(SOURCE / "application" / file)
        assert not any(
            name.startswith(("gaiavfx.application.recast", "gaiavfx.modules.editing"))
            for name in imports
        ), (file, imports)
    # Only the two human commands start it; reading the recast state never does.
    starters = {
        path.name
        for path in (SOURCE / "application").rglob("*.py")
        if "start_dialogue_analysis(" in path.read_text("utf-8")
    }
    assert starters == {"dialogue_analysis.py", "recast_lines.py"}, starters


def test_voice_build_does_not_depend_on_recasts():
    """ADR 0016 §4: the shared voice build is Library evidence; it knows no recast, no Editing table and no store."""
    for file in ("voice_build.py", "voice_steps.py"):
        imports = list(imported_names(SOURCE / "application" / file))
        assert not any(
            name.startswith(
                (
                    "gaiavfx.application.recast",
                    "gaiavfx.modules.editing",
                    "gaiavfx.api",
                    "sqlalchemy",
                )
            )
            or ".persistence" in name
            for name in imports
        ), (file, imports)
    # Only the human voice commands start a build or an audition; reading recasts and the feed never do.
    for call in ("start_voice_build(", "start_voice_audition("):
        starters = {
            path.name
            for path in (SOURCE / "application").rglob("*.py")
            if call in path.read_text("utf-8")
        }
        assert "voice_build.py" in starters, (call, starters)
        assert starters <= {"voice_build.py", "recast_voice.py"}, (call, starters)


def test_harness_never_imports_recast_human_commands():
    human = {
        "confirm_appearance",
        "reject_appearance",
        "clear_appearance",
        "_decide_appearance",
        "choose_version",
        "start_head_pass",
        "confirm_target",
        "approve_reference",
        "reject_reference",
        "set_target",
        "clear_target",
        "_target",
        "start_conform",
        "set_not_same",
        "undo_edit",
        "start_dialogue_analysis",
        "retry_dialogue_analysis",
        "confirm_line",
        "reject_line",
        "clear_line",
        "edge_line",
        "confirm_lines",
        "set_voice",
        "clear_voice",
        "confirm_voice",
        "build_voice",
        "retry_voice",
        "approve_voice",
        "reject_voice",
    }
    for file in (SOURCE / "application" / "harness").rglob("*.py"):
        tree = ast.parse(file.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                ("recast", "recast_conform", "recast_edits", "recast_lines", "recast_voice")
            ):
                assert not ({a.name for a in node.names} & (human | {"*"})), file
            if isinstance(node, ast.Attribute):
                assert node.attr not in human, file


def test_harness_never_reaches_set_human_commands():
    """ADR 0018 §5: importing, accepting and discarding set versions, and the human set tools, are the user's."""
    human = {
        "import_bundle",
        "read_bundle",
        "accept_version",
        "discard_version",
        "accept",
        "discard",
        "set_issue_status",
        "set_hide_region_removed",
        "set_view_removed",
        # running fixes is the human's until the agent's repair_set arrives inside approved plans (ADR 0018 §7)
        "run_queued_fixes",
        "start_set_build",
        "retry_fix",
        "start_fixes",
        "end_fixes",
        "settle_issues",
    }
    for file in (SOURCE / "application" / "harness").rglob("*.py"):
        tree = ast.parse(file.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                (
                    "application.sets",
                    "application.set_bundles",
                    "application.set_build",
                    "modules.sets.service",
                )
            ):
                assert not ({a.name for a in node.names} & (human | {"*"})), file
            if isinstance(node, ast.Attribute):
                assert node.attr not in human - {"accept", "discard"}, file


def test_sets_module_keeps_to_its_records():
    """ADR 0018 §1: the sets module imports its own records, the database helpers and shared errors only."""
    allowed = ("gaiavfx.modules.sets", "gaiavfx.infrastructure.database", "gaiavfx.shared")
    for file in (SOURCE / "modules" / "sets").rglob("*.py"):
        tree = ast.parse(file.read_text("utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and (node.module or "").startswith("gaiavfx")
            ):
                assert node.module.startswith(allowed), (file, node.module)


def test_film_conversation_tools_are_adr_0012_section_8():
    """ADR 0012 Amendment 7 §8: a film's agent reads, proposes, plans and starts runs; nothing else."""
    from gaiavfx.application.harness.scope import FILM_TOOLS
    from gaiavfx.application.harness.tools import DEFAULT_TOOLS
    from gaiavfx.application.harness.tools.common import TOOL_KINDS

    assert FILM_TOOLS <= {tool.name for tool in DEFAULT_TOOLS}
    assert not {name for name in FILM_TOOLS if TOOL_KINDS.get(name) in {"edit", "prepare", "pick"}}
    assert {name for name in FILM_TOOLS if name in TOOL_KINDS} == {"start_recast_run"}
    movie_tools = {
        "read_movie",
        "read_segment",
        "inspect_cut",
        "draft_storyline",
        "prepare_cut",
        "read_candidates",
        "render_candidates",
        "analyze_people",
        "replace_character",
        "pick_provisional",
        "analyze_reference_video",
    }
    assert not FILM_TOOLS & movie_tools
    assert not {name for name in FILM_TOOLS if name.startswith("edit_")}
    # The scope seam reads only the harness, its recast reader, the Library and shared code.
    for name in imported_names(SOURCE / "application" / "harness" / "scope.py"):
        assert name.split(".")[0] in sys.stdlib_module_names or name.startswith(
            (
                "gaiavfx.application.harness",
                "gaiavfx.application.recast",
                "gaiavfx.modules.library.media_application",
                "gaiavfx.shared",
            )
        ), name
