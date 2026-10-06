"""Apply a restricted RFC 6902 patch to doctor-editable case sheet fields."""

import copy
import re
from typing import Any

from app.domain.case_sheet import CaseSheet

EDITABLE_ROOTS = frozenset(
    {
        "chief_complaint",
        "hpi",
        "past_history",
        "medications",
        "allergies",
        "family_history",
        "social",
        "women_health",
    }
)


class CaseSheetPatchError(ValueError):
    """A patch is malformed, targets a protected field, or fails schema validation."""


def _pointer_tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/") or len(pointer) > 500:
        raise CaseSheetPatchError("Each patch path must be an absolute JSON Pointer.")
    tokens = pointer[1:].split("/")
    if any(re.search(r"~(?![01])", token) for token in tokens):
        raise CaseSheetPatchError("The patch contains an invalid JSON Pointer escape.")
    return [token.replace("~1", "/").replace("~0", "~") for token in tokens]


def _array_index(token: str, length: int, *, inserting: bool) -> int:
    # RFC 6902 uses this JSON Pointer token to append to an array.
    if inserting and token == "-":  # nosec B105
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", token):
        raise CaseSheetPatchError("Array paths must use a valid numeric index.")
    index = int(token)
    if index > length or (index == length and not inserting):
        raise CaseSheetPatchError("The patch path does not exist in the current case sheet.")
    return index


def _parent_for(
    document: dict[str, Any], tokens: list[str]
) -> tuple[dict[str, Any] | list[Any], str]:
    if not tokens or tokens[0] not in EDITABLE_ROOTS:
        raise CaseSheetPatchError("This case sheet field is read-only.")
    parent: dict[str, Any] | list[Any] = document
    for token in tokens[:-1]:
        if isinstance(parent, dict) and token in parent:
            child = parent[token]
        elif isinstance(parent, list):
            child = parent[_array_index(token, len(parent), inserting=False)]
        else:
            raise CaseSheetPatchError("The patch path does not exist in the current case sheet.")
        if not isinstance(child, (dict, list)):
            raise CaseSheetPatchError("A patch path cannot traverse through a scalar value.")
        parent = child
    return parent, tokens[-1]


def _read(parent: dict[str, Any] | list[Any], token: str) -> Any:
    if isinstance(parent, dict):
        if token not in parent:
            raise CaseSheetPatchError("The patch path does not exist in the current case sheet.")
        return copy.deepcopy(parent[token])
    return copy.deepcopy(parent[_array_index(token, len(parent), inserting=False)])


def _apply_operation(document: dict[str, Any], operation: dict[str, Any]) -> None:
    op = operation.get("op")
    tokens = _pointer_tokens(operation.get("path", ""))
    parent, token = _parent_for(document, tokens)
    if op == "add":
        value = copy.deepcopy(operation["value"])
        if isinstance(parent, dict):
            parent[token] = value
            return
        parent.insert(_array_index(token, len(parent), inserting=True), value)
        return
    if op == "replace":
        _read(parent, token)
        value = copy.deepcopy(operation["value"])
        if isinstance(parent, dict):
            parent[token] = value
        else:
            parent[_array_index(token, len(parent), inserting=False)] = value
        return
    if op == "remove":
        _read(parent, token)
        if isinstance(parent, dict):
            del parent[token]
        else:
            del parent[_array_index(token, len(parent), inserting=False)]
        return
    raise CaseSheetPatchError("Only add, replace, and remove operations are supported.")


def apply_case_sheet_patch(
    current: dict[str, Any], operations: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    if not operations or len(operations) > 100:
        raise CaseSheetPatchError("Provide between one and 100 patch operations.")
    updated = copy.deepcopy(current)
    changed_paths: list[str] = []
    metadata = updated.setdefault("field_meta", {})
    if not isinstance(metadata, dict):
        raise CaseSheetPatchError("Case sheet field metadata is invalid.")
    for operation in operations:
        if "path" not in operation or operation.get("op") not in {"add", "replace", "remove"}:
            raise CaseSheetPatchError("Each patch operation must have a supported op and path.")
        if operation["op"] in {"add", "replace"} and "value" not in operation:
            raise CaseSheetPatchError("Add and replace operations require a value.")
        path = operation["path"]
        _apply_operation(updated, operation)
        updated["field_meta"][path] = {
            "source": "doctor",
            "confidence": 1.0,
            "confirmed": True,
        }
        changed_paths.append(path)
    try:
        validated = CaseSheet.model_validate(updated)
    except Exception as exc:
        raise CaseSheetPatchError("The updated case sheet does not match its schema.") from exc
    return validated.model_dump(mode="json"), changed_paths
