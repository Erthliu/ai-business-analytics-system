"""Domain exceptions for safe profiling boundaries."""


class DatasetNotProfileableError(RuntimeError):
    """Raised when REJECT or QUARANTINE data is requested for profiling."""
