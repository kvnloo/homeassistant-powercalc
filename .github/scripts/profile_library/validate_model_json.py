from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

COMMENT_MARKER = "<!-- model.json validate action comment -->"
PROFILE_DIRECTORY = "profile_library"


@dataclass(frozen=True)
class ProfileJson:
    """A profile library JSON file kind and where it lives inside the library."""

    file_name: str
    # Number of path parts, including the profile_library directory itself:
    # profile_library/<manufacturer>/<model>/model.json is 4, one less for manufacturer.json.
    path_length: int


MODEL_JSON = ProfileJson(file_name="model.json", path_length=4)
MANUFACTURER_JSON = ProfileJson(file_name="manufacturer.json", path_length=3)
PROFILE_JSON_KINDS = (MODEL_JSON, MANUFACTURER_JSON)


def _format_path(error: ValidationError) -> str:
    if not error.absolute_path:
        return "$"

    return "$." + ".".join(str(part) for part in error.absolute_path)


def _format_error(error: ValidationError) -> str:
    details = [
        f"- Path: `{_format_path(error)}`",
        f"- Validator: `{error.validator}`",
        f"- Message:\n\n  ```text\n  {error.message}\n  ```",
    ]

    return "\n".join(details)


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _load_changed_files(path: Path) -> list[Path]:
    return [Path(filename) for filename in cast(list[str], _load_json(path))]


def _profile_json_files(changed_files: list[Path], profile_json: ProfileJson) -> list[Path]:
    return [
        path
        for path in changed_files
        if len(path.parts) == profile_json.path_length
        and path.parts[0] == PROFILE_DIRECTORY
        and path.parts[-1] == profile_json.file_name
        and path.is_file()
    ]


def _missing_manufacturer_json(changed_files: list[Path]) -> list[Path]:
    """Return manufacturer.json paths required by a changed model.json but absent on disk.

    Profile PRs that introduce a new manufacturer directory often only add model.json
    and LUT files. Schema validation never sees manufacturer.json in that case, so
    require the sibling file to exist whenever a model.json is in the change set.
    """
    missing: list[Path] = []
    seen: set[Path] = set()
    for path in _profile_json_files(changed_files, MODEL_JSON):
        manufacturer_json = path.parents[1] / MANUFACTURER_JSON.file_name
        if manufacturer_json in seen:
            continue
        seen.add(manufacturer_json)
        if not manufacturer_json.is_file():
            missing.append(manufacturer_json)
    return missing


def _validate_files(changed_files: list[Path], schemas: dict[ProfileJson, Path]) -> dict[Path, list[ValidationError]]:
    errors_by_file: dict[Path, list[ValidationError]] = {}
    for profile_json, schema_path in schemas.items():
        validator = Draft202012Validator(cast(dict[str, object], _load_json(schema_path)))
        for path in _profile_json_files(changed_files, profile_json):
            errors = sorted(validator.iter_errors(_load_json(path)), key=lambda error: list(error.absolute_path))
            if errors:
                errors_by_file[path] = errors

    return errors_by_file


def _validated_file_names() -> str:
    return " and ".join(f"`{profile_json.file_name}`" for profile_json in PROFILE_JSON_KINDS)


def _build_report(
    errors_by_file: dict[Path, list[ValidationError]],
    missing_manufacturer: list[Path],
) -> str:
    if not errors_by_file and not missing_manufacturer:
        return f"{COMMENT_MARKER}\n\nAll changed {_validated_file_names()} files are valid."

    sections = [COMMENT_MARKER]
    if missing_manufacturer:
        sections.append(
            "Changed `model.json` files require `manufacturer.json` in the same manufacturer directory."
        )
        for path in missing_manufacturer:
            sections.append(f"## `{path}`")
            sections.append(
                "Missing. Add required `name` plus optional `full_name` / `website` / `country` / `aliases`."
            )
    if errors_by_file:
        sections.append(f"JSON Schema validation failed for changed {_validated_file_names()} files.")
        for path, errors in errors_by_file.items():
            sections.append(f"## `{path}`")
            sections.extend(_format_error(error) for error in errors)

    return "\n\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-schema", type=Path, required=True)
    parser.add_argument("--manufacturer-schema", type=Path, required=True)
    parser.add_argument("--changed-files", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()

    changed_files = _load_changed_files(args.changed_files)
    errors_by_file = _validate_files(
        changed_files,
        {MODEL_JSON: args.model_schema, MANUFACTURER_JSON: args.manufacturer_schema},
    )
    missing_manufacturer = _missing_manufacturer_json(changed_files)
    failed = bool(errors_by_file or missing_manufacturer)

    args.report.write_text(_build_report(errors_by_file, missing_manufacturer), encoding="utf-8")
    args.status.write_text("failure" if failed else "success", encoding="utf-8")

    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
