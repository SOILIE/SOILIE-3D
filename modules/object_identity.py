"""Canonical, reversible object identities for collector relation rows.

Relation CSVs must keep the semantic class separate from the source instance
identifier.  Delimiter-based labels cannot do that safely because real class
names and identifiers may themselves contain commas, underscores, or colons.
"""

from __future__ import annotations

import json
import re
import unicodedata


COLLECTOR_OBJECT_LABEL_PREFIX = "soilie-object-v1:"


class ObjectIdentityError(ValueError):
    """A collector object label is missing, malformed, or non-canonical."""


def normalize_object_class(value: str) -> str:
    """Return the stable model class spelling used by prepared catalogues."""

    if not isinstance(value, str):
        raise ObjectIdentityError("Object class must be a string")
    normalized = re.sub(r"\s+", "_", unicodedata.normalize("NFC", value).strip())
    if not normalized:
        raise ObjectIdentityError("Object class must not be empty")
    return normalized


def normalize_instance_id(value: str) -> str:
    """Return a stable non-empty source instance identifier."""

    if not isinstance(value, str):
        raise ObjectIdentityError("Object instance ID must be a string")
    # JSON provides the boundary, so internal whitespace remains meaningful:
    # ``bathroom left`` and ``bath roomleft`` must never collapse together.
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise ObjectIdentityError("Object instance ID must not be empty")
    return normalized


def encode_collector_object_label(object_class: str, instance_id: str) -> str:
    """Encode one normalized class/instance pair without delimiter ambiguity."""

    identity = [
        normalize_object_class(object_class),
        normalize_instance_id(instance_id),
    ]
    return COLLECTOR_OBJECT_LABEL_PREFIX + json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_collector_object_label(label: str) -> tuple[str, str]:
    """Decode a canonical collector label and reject legacy/alternate forms."""

    if not isinstance(label, str) or not label.startswith(COLLECTOR_OBJECT_LABEL_PREFIX):
        raise ObjectIdentityError(
            "Collector object labels must use the soilie-object-v1 identity codec",
        )
    payload = label[len(COLLECTOR_OBJECT_LABEL_PREFIX):]
    try:
        identity = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ObjectIdentityError("Collector object label contains invalid JSON") from exc
    if (
        not isinstance(identity, list)
        or len(identity) != 2
        or not all(isinstance(value, str) for value in identity)
    ):
        raise ObjectIdentityError(
            "Collector object label must contain a class and instance ID",
        )
    canonical = encode_collector_object_label(identity[0], identity[1])
    if canonical != label:
        raise ObjectIdentityError("Collector object label is not canonical")
    return identity[0], identity[1]
