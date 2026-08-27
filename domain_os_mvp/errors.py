class DomainOsError(RuntimeError):
    """Base error for the MVP kernel."""


class NotFoundError(DomainOsError):
    pass


class CapabilityError(DomainOsError):
    pass


class PreconditionError(DomainOsError):
    pass


class FrozenResourceError(DomainOsError):
    pass


class OperationConflictError(DomainOsError):
    pass


class InvalidActionError(DomainOsError, ValueError):
    pass


class InvalidStateError(DomainOsError):
    pass
