from __future__ import annotations

from pathlib import Path
import unicodedata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL_ICON_ROOT = PROJECT_ROOT / "coryn_skill_icons"

TREE_FOLDER_ALIASES = {
    "magicwarrior": "magicblade",
    "blacksmith": "smith",
}


def normalize_icon_key(value: str) -> str:
    """Return a punctuation-insensitive key for deterministic icon matching."""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _tree_folder_key(tree_name: str) -> str:
    key = normalize_icon_key(tree_name)
    if key.endswith("skills"):
        key = key[:-len("skills")]
    return TREE_FOLDER_ALIASES.get(key, key)


class SkillIconCatalog:
    """Lazily index checked-in skill icons and resolve them deterministically."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._folder_index: dict[str, dict[str, tuple[Path, ...]]] | None = None
        self._global_index: dict[str, tuple[Path, ...]] | None = None

    def _ensure_index(self) -> None:
        if self._folder_index is not None and self._global_index is not None:
            return

        folder_lists: dict[str, dict[str, list[Path]]] = {}
        global_lists: dict[str, list[Path]] = {}

        if self.root.is_dir():
            for folder in sorted(self.root.iterdir(), key=lambda path: path.name.casefold()):
                if not folder.is_dir():
                    continue
                folder_key = normalize_icon_key(folder.name)
                local = folder_lists.setdefault(folder_key, {})
                for icon in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
                    if not icon.is_file() or icon.suffix.casefold() != ".png":
                        continue
                    skill_key = normalize_icon_key(icon.stem)
                    if not skill_key:
                        continue
                    local.setdefault(skill_key, []).append(icon)
                    global_lists.setdefault(skill_key, []).append(icon)

        self._folder_index = {
            folder_key: {
                skill_key: tuple(paths)
                for skill_key, paths in skill_map.items()
            }
            for folder_key, skill_map in folder_lists.items()
        }
        self._global_index = {
            skill_key: tuple(paths)
            for skill_key, paths in global_lists.items()
        }

    def resolve(self, tree_name: str, skill_name: str) -> Path | None:
        self._ensure_index()
        assert self._folder_index is not None
        assert self._global_index is not None

        skill_key = normalize_icon_key(skill_name)
        if not skill_key:
            return None

        folder_key = _tree_folder_key(tree_name)
        local_matches = self._folder_index.get(folder_key, {}).get(skill_key, ())
        if len(local_matches) == 1:
            return local_matches[0]
        if len(local_matches) > 1:
            return None

        global_matches = self._global_index.get(skill_key, ())
        if len(global_matches) == 1:
            return global_matches[0]
        return None


DEFAULT_SKILL_ICON_CATALOG = SkillIconCatalog(DEFAULT_SKILL_ICON_ROOT)


__all__ = [
    "DEFAULT_SKILL_ICON_CATALOG",
    "DEFAULT_SKILL_ICON_ROOT",
    "SkillIconCatalog",
    "TREE_FOLDER_ALIASES",
    "normalize_icon_key",
]
