from pydantic import BaseModel


class SearchHit(BaseModel):
    id: str
    name: str | None
    # Scope level, principal type, or role type (BuiltInRole / CustomRole).
    kind: str | None


class SearchResults(BaseModel):
    query: str
    scopes: list[SearchHit]
    principals: list[SearchHit]
    roles: list[SearchHit]
