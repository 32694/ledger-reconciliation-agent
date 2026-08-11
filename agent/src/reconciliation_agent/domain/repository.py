"""Repository boundary for reconciliation persistence."""

from sqlalchemy.orm import Session


class CaseNotFoundError(Exception):
    """Raised when a requested reconciliation case does not exist."""


class CaseAlreadyResolvedError(Exception):
    """Raised when a reconciliation case already has a resolution."""


class ReconciliationRepository:
    """Persistence boundary for reconciliation data."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to a SQLAlchemy session."""
        self.session = session
