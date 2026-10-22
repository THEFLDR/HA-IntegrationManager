"""Strict catalog mapping validation."""

import re
from dataclasses import dataclass

from .const import DOMAIN, OWNER, REPOSITORY

DOMAIN_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
REPO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")


@dataclass(frozen=True)
class CatalogEntry:
    """Distinct integration identity and display name."""

    name: str
    repository: str
    domain: str


def validate_repository(repository: str) -> str:
    """Allow only an owner's public GitHub repository identity, never URLs."""
    parts = repository.split("/")
    if (
        len(parts) != 2
        or parts[0] != OWNER
        or not REPO_PATTERN.fullmatch(parts[1])
        or parts[1] in {".", ".."}
    ):
        raise ValueError("Repository must belong to THEFLDR")
    return repository


def parse_catalog(payload: object) -> list[CatalogEntry]:
    """Reject ambiguous, duplicate, or incomplete mappings as a whole."""
    if not isinstance(payload, list) or len(payload) > 500:
        raise ValueError("Catalog must be a list of at most 500 integrations")
    domains, repositories, result = set(), set(), []
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"name", "repository", "domain"}:
            raise ValueError("Catalog requires name, repository and domain")
        name, repo, domain = (item[k] for k in ("name", "repository", "domain"))
        if not all(isinstance(value, str) for value in (name, repo, domain)):
            raise ValueError("Catalog mappings must be strings")
        if not name.strip() or name != name.strip() or len(name) > 100:
            raise ValueError("Invalid integration display name")
        if any(ord(char) < 32 for char in name):
            raise ValueError("Invalid integration display name")
        validate_repository(repo)
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError("Invalid integration domain")
        if domain in domains or repo.casefold() in repositories:
            raise ValueError("Duplicate catalog domain or repository")
        domains.add(domain)
        repositories.add(repo.casefold())
        if domain == DOMAIN or repo.casefold() == REPOSITORY.casefold():
            raise ValueError("Manager cannot be an installable catalog entry")
        result.append(CatalogEntry(name, repo, domain))
    return result
