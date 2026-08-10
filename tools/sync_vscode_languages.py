from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _discover_app_root() -> Path:
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        install_root = Path(local_app_data) / "Programs" / "Microsoft VS Code"
        if install_root.exists():
            candidates.extend(
                path / "resources" / "app"
                for path in install_root.iterdir()
                if path.is_dir()
            )
            candidates.append(install_root / "resources" / "app")
    for candidate in candidates:
        if (candidate / "extensions").is_dir() and (candidate / "product.json").is_file():
            return candidate
    raise SystemExit(
        "VS Code app root was not found. Pass --vscode-app-root or --extensions-root."
    )


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def build_registry(extensions_roots: list[Path], product_path: Path | None) -> dict[str, Any]:
    product: dict[str, Any] = {}
    if product_path and product_path.is_file():
        product = json.loads(product_path.read_text(encoding="utf-8"))

    merged: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "aliases": [],
            "extensions": [],
            "filenames": [],
            "filename_patterns": [],
            "first_lines": [],
            "contributors": [],
        }
    )
    package_paths = sorted(
        {
            package_path
            for extensions_root in extensions_roots
            for package_path in extensions_root.glob("*/package.json")
        }
    )
    for package_path in package_paths:
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        contributions = package.get("contributes")
        languages = contributions.get("languages") if isinstance(contributions, dict) else None
        if not isinstance(languages, list):
            continue
        contributor = str(package.get("name") or package_path.parent.name)
        for language in languages:
            if not isinstance(language, dict):
                continue
            language_id = language.get("id")
            if not isinstance(language_id, str) or not language_id:
                continue
            target = merged[language_id]
            target["aliases"].extend(_strings(language.get("aliases")))
            target["extensions"].extend(_strings(language.get("extensions")))
            target["filenames"].extend(_strings(language.get("filenames")))
            target["filename_patterns"].extend(_strings(language.get("filenamePatterns")))
            first_line = language.get("firstLine")
            if isinstance(first_line, str) and first_line:
                target["first_lines"].append(first_line)
            target["contributors"].append(contributor)

    languages = []
    for language_id, values in sorted(merged.items()):
        languages.append(
            {
                "id": language_id,
                **{
                    key: sorted(set(items), key=lambda item: (item.casefold(), item))
                    for key, items in values.items()
                },
            }
        )
    return {
        "schema_version": "1.0",
        "registry_revision": str(product.get("commit") or product.get("version") or "code-oss"),
        "source": "microsoft/vscode contributes.languages",
        "source_repository": "https://github.com/microsoft/vscode",
        "source_version": product.get("version"),
        "source_commit": product.get("commit"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "license": "MIT",
        "language_count": len(languages),
        "languages": languages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Vision's language registry from Code-OSS language contributions."
    )
    parser.add_argument("--vscode-app-root", type=Path)
    parser.add_argument(
        "--extensions-root",
        type=Path,
        action="append",
        help="Extension root to scan; repeat to merge built-in and private extensions.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/data/vscode_languages.json"),
    )
    args = parser.parse_args()

    app_root = args.vscode_app_root or _discover_app_root()
    extensions_roots = list(args.extensions_root or [])
    if app_root:
        extensions_roots.insert(0, app_root / "extensions")
    for extensions_root in extensions_roots:
        if not extensions_root.is_dir():
            raise SystemExit(f"Extensions root does not exist: {extensions_root}")
    product_path = app_root / "product.json" if app_root else None
    registry = build_registry(extensions_roots, product_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {registry['language_count']} languages to {args.output} "
        f"from {registry['source_version'] or ', '.join(map(str, extensions_roots))}"
    )


if __name__ == "__main__":
    main()
