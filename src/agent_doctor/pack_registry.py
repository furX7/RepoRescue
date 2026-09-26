"""Explicit, ordered Pack registration; no discovery or stage execution.

Membership belongs to each registry instance. Snapshots freeze membership/order,
not the supplied Pack objects; authors must keep their declarations stable.
"""

from collections.abc import Sequence
from dataclasses import replace

from .extensions import Extension, ExtensionMetadata, validate_pack


class PackRegistry:
    """Save explicitly supplied, structurally valid Packs in registration order.

    Registration errors are ValueError. Runtime compatibility remains Core's
    responsibility, and no execution authority is provided to registered Packs.
    """

    def __init__(self, packs: Sequence[Extension] = ()) -> None:
        self._packs: dict[str, Extension] = {}
        for pack in packs:
            self.register(pack)

    @classmethod
    def default(cls) -> "PackRegistry":
        """Create independent membership using Core's single built-in source.

        The fixed Core import is lazy; importing the SDK does not load the
        pipeline or Python Pack. No caller-supplied module names are imported.
        """
        from .extension_pipeline import BUILTIN_EXTENSIONS

        return cls(BUILTIN_EXTENSIONS)

    def register(self, pack: Extension) -> None:
        """Validate declarations only, then add once; never call Pack stages."""
        metadata = getattr(pack, "metadata", None)
        if not isinstance(metadata, ExtensionMetadata):
            raise ValueError("Pack metadata must be an ExtensionMetadata instance")
        try:
            # Reuse construction checks even for objects whose creation bypassed
            # the frozen dataclass validator. Do not replace the author's object.
            replace(metadata)
            for field in ("supported_platforms", "required_tools"):
                values = getattr(metadata, field)
                if not isinstance(values, tuple) or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    raise ValueError(f"{field} must be a tuple of non-empty strings")
            if metadata.id in self._packs:
                raise ValueError(f"Duplicate pack ID: {metadata.id!r}")
            validate_pack(pack)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Pack {metadata.id!r} registration failed: {error}") from error
        self._packs[metadata.id] = pack

    def get(self, pack_id: str) -> Extension | None:
        """Return the supplied object, or None when its registration ID is absent."""
        return self._packs.get(pack_id)

    @property
    def packs(self) -> tuple[Extension, ...]:
        return self.snapshot()

    def snapshot(self) -> tuple[Extension, ...]:
        """Capture read-only membership in deterministic registration order."""
        return tuple(self._packs.values())
