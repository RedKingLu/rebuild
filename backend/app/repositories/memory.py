"""In-memory repository implementation.

R4: Pure dict-based storage. No ORM. No SQL. No persistence.
Data is lost on restart (marked persistence: volatile).
"""

from typing import TypeVar, Optional

from app.repositories.base import AbstractRepository

T = TypeVar("T")


class InMemoryRepository(AbstractRepository[T]):
    """Dict-based in-memory repository.

    NOT a future database structure. When migrating to PostgreSQL,
    implement the same AbstractRepository ABC with SQLAlchemy or similar.
    Do NOT evolve this class into an ORM.
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._store: dict[str, T] = {}
        self._id_field = "id"

    def list(self, **filters) -> list[T]:
        results = list(self._store.values())
        for key, value in filters.items():
            results = [r for r in results if _get_field(r, key) == value]
        return results

    def get(self, entity_id: str) -> Optional[T]:
        return self._store.get(entity_id)

    def create(self, entity: T) -> T:
        eid = _get_field(entity, self._id_field) or _get_field(entity, "project_id") or _get_field(entity, "run_id")
        if not eid:
            raise ValueError(f"Entity has no identifiable id field: {entity}")
        self._store[str(eid)] = entity
        return entity

    def update(self, entity_id: str, **fields) -> Optional[T]:
        entity = self._store.get(entity_id)
        if entity is None:
            return None
        if isinstance(entity, dict):
            entity.update(fields)
        else:
            for k, v in fields.items():
                if hasattr(entity, k):
                    setattr(entity, k, v)
        return entity

    def delete(self, entity_id: str) -> bool:
        if entity_id in self._store:
            del self._store[entity_id]
            return True
        return False

    def clear(self) -> None:
        self._store.clear()


def _get_field(obj, field_name: str):
    if isinstance(obj, dict):
        return obj.get(field_name)
    return getattr(obj, field_name, None)
