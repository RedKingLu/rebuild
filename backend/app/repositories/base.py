"""Abstract repository base class."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional

T = TypeVar("T")


class AbstractRepository(ABC, Generic[T]):
    """Abstract base for all domain repositories.

    R4: Only InMemoryRepository implementation exists.
    No ORM, no SQLAlchemy, no Alembic.
    Future PostgreSQL migration: implement this same ABC.
    """

    @abstractmethod
    def list(self, **filters) -> list[T]:
        ...

    @abstractmethod
    def get(self, entity_id: str) -> Optional[T]:
        ...

    @abstractmethod
    def create(self, entity: T) -> T:
        ...

    @abstractmethod
    def update(self, entity_id: str, **fields) -> Optional[T]:
        ...

    @abstractmethod
    def delete(self, entity_id: str) -> bool:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...
