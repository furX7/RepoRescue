"""Explicit discovery of installed Pack factories, not a sandbox or installer.

Only installed entry-point metadata is queried. Loading and invoking factories
runs trusted third-party Python code; Core does not invoke Pack business stages.
"""

from dataclasses import dataclass
from importlib import metadata
import re
from typing import Literal

from .extensions import ExtensionMetadata
from .pack_registry import PackRegistry


PACK_ENTRY_POINT_GROUP = "reporescue.packs"


@dataclass(frozen=True)
class DiscoveryFailure:
    entry_point: str | None
    distribution: str | None
    code: Literal[
        "query_failed", "metadata_failed", "load_failed", "invalid_factory",
        "factory_failed", "invalid_pack", "duplicate_pack_id",
    ]
    message: str

    @property
    def status(self) -> Literal["skipped", "failed"]:
        return "skipped" if self.code == "duplicate_pack_id" else "failed"


@dataclass(frozen=True)
class DiscoveryResult:
    registered_ids: tuple[str, ...]
    failures: tuple[DiscoveryFailure, ...]

    @property
    def skipped(self) -> tuple[DiscoveryFailure, ...]:
        return tuple(item for item in self.failures if item.status == "skipped")

    @property
    def failed(self) -> tuple[DiscoveryFailure, ...]:
        return tuple(item for item in self.failures if item.status == "failed")


def _public_identity(value: str) -> str | None:
    """Keep concise metadata labels; omit paths and credential-shaped labels."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) and not re.match(
        r"(?:sk-|gh[pousr]_|AKIA)", value,
    ):
        return value
    return None


def _distribution_name(entry_point: metadata.EntryPoint) -> str:
    try:
        name = entry_point.dist.name
        return name if isinstance(name, str) else ""
    except Exception:
        return ""


def discover_installed_packs(registry: PackRegistry) -> DiscoveryResult:
    """Load zero-argument factories explicitly into the caller's registry.

    Order is entry-point name, distribution name (empty if unavailable), value.
    Registration validity uses PackRegistry; runtime compatibility stays in the
    pipeline. Ordinary failures are isolated without exposing exception text.
    BaseException signals propagate. Successful earlier registrations survive.
    """
    if not isinstance(registry, PackRegistry):
        raise TypeError("registry must be a PackRegistry instance")
    try:
        points = tuple(metadata.entry_points(group=PACK_ENTRY_POINT_GROUP))
    except Exception:
        return DiscoveryResult((), (DiscoveryFailure(
            None, None, "query_failed", "Installed Pack metadata could not be queried.",
        ),))

    failures = []
    candidates = []
    for point in points:
        try:
            name, value = point.name, point.value
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError("Invalid entry-point metadata")
            candidates.append((name, _distribution_name(point), value, point))
        except Exception:
            failures.append(DiscoveryFailure(
                None, None, "metadata_failed", "Entry-point metadata is invalid.",
            ))

    registered_ids = []
    for name, distribution, _, point in sorted(candidates, key=lambda item: item[:3]):
        identity = _public_identity(name), _public_identity(distribution)
        try:
            factory = point.load()
        except Exception:
            failures.append(DiscoveryFailure(*identity, "load_failed", "Entry point could not be loaded."))
            continue
        if not callable(factory):
            failures.append(DiscoveryFailure(*identity, "invalid_factory", "Entry point must target a callable factory."))
            continue
        try:
            pack = factory()
        except Exception:
            failures.append(DiscoveryFailure(*identity, "factory_failed", "No-argument Pack factory failed."))
            continue
        duplicate = False
        try:
            declaration = getattr(pack, "metadata", None)
            duplicate = (not isinstance(pack, type) and isinstance(declaration, ExtensionMetadata)
                         and isinstance(declaration.id, str) and registry.get(declaration.id) is not None)
            registry.register(pack)
        except Exception as error:
            duplicate = duplicate and isinstance(error, ValueError)
            code = "duplicate_pack_id" if duplicate else "invalid_pack"
            message = "Pack ID is already registered." if duplicate else "Factory result failed Pack registration validation."
            failures.append(DiscoveryFailure(*identity, code, message))
            continue
        registered_ids.append(declaration.id)
    return DiscoveryResult(tuple(registered_ids), tuple(failures))
