class ApplicationError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class ObservationReadError(ApplicationError):
    """A valid observation range unexpectedly yielded no decoded frames."""
