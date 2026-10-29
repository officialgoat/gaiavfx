"""Small observation boundary. Creative rules never depend on a telemetry vendor."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature

_active = ContextVar("gaiavfx_observation", default=None)
_workflow = ContextVar("gaiavfx_workflow", default=None)


class QuietObservation:
    id = None
    trace_id = None
    context = None

    def update(self, **values):
        pass

    def media(self, path, **metadata):
        pass


QUIET = QuietObservation()


def current_observation():
    return _active.get() or QUIET


def trace_context():
    active = _active.get()
    workflow = _workflow.get()
    return dict(active.context) if active else dict(workflow[1]) if workflow else None


@contextmanager
def observation(name, *, input=None, metadata=None, as_type="span"):
    parent = _active.get()
    workflow = _workflow.get()
    if parent is None and workflow:
        recorder, context = workflow
        with recorder.observe(
            name,
            input=input,
            metadata=metadata,
            as_type=as_type,
            context=recorder.new_context(inherited=context),
            root=True,
        ) as span:
            yield span
    elif parent is None:
        yield QUIET
    else:
        with parent.recorder.observe(name, input=input, metadata=metadata, as_type=as_type) as span:
            yield span


def observed(name, *, capture=True, as_type="span", media_inputs=(), media_outputs=()):
    """Explicit use-case/stage instrumentation, active only inside a workflow."""

    def decorate(function):
        parameters = signature(function)

        @wraps(function)
        def call(*args, **kwargs):
            if _active.get() is None and _workflow.get() is None:
                return function(*args, **kwargs)
            bound = dict(parameters.bind(*args, **kwargs).arguments)
            inputs = None
            if capture:
                inputs = dict(bound)
                for key in ("self", "runner", "w", "workspace", "session", "cancellation"):
                    inputs.pop(key, None)
            with observation(name, input=inputs, as_type=as_type) as span:
                for key in media_inputs:
                    if bound.get(key) is not None:
                        span.media(bound[key], field="input", role=key)
                result = function(*args, **kwargs)
                for key in media_outputs:
                    if bound.get(key) is not None:
                        span.media(bound[key], field="output", role=key)
                if capture:
                    span.update(output=result)
                return result

        return call

    return decorate
