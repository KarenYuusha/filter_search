from pathlib import Path

path = Path("discord_bot.py")
text = path.read_text(encoding="utf-8")
old = '''        path = Path(str(local_path))
        if not path.is_absolute():
            path = (database_path.parent / path).resolve()
        if path.is_file():
            output.append(path)
'''
new = '''        path = Path(str(local_path))
        if path.is_absolute():
            resolved_path = path
        elif path.parts and path.parts[0].casefold() == "appearance":
            resolved_path = (database_path.parent.parent / path).resolve()
        else:
            resolved_path = (database_path.parent / path).resolve()
        if resolved_path.is_file():
            output.append(resolved_path)
'''
if old not in text:
    raise SystemExit("target image-path block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
