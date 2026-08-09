from pathlib import Path

paths = [
    Path("docs/superpowers/specs/2026-08-10-upgrade-hybrid-display-design.md"),
    Path("docs/superpowers/plans/2026-08-10-upgrade-hybrid-display.md"),
]

path_replacements = [
    (
        "1. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
        "2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
    ),
    (
        "2. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
        "1. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
    ),
]

old_tree = """Don
├── Don Upgrade A
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Alternative
    └── Don Upgrade C  ↩ already shown"""
new_tree = """Don
├── Don Alternative
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Upgrade A
    └── Don Upgrade C  ↩ already shown"""

old_tuple_tree = """        \"Don\",
        \"├── Don Upgrade A\",
        \"│   └── Don Upgrade C\",
        \"│       └── Don Upgrade F  ◀ selected\",
        \"└── Don Alternative\",
        \"    └── Don Upgrade C  ↩ already shown\","""
new_tuple_tree = """        \"Don\",
        \"├── Don Alternative\",
        \"│   └── Don Upgrade C\",
        \"│       └── Don Upgrade F  ◀ selected\",
        \"└── Don Upgrade A\",
        \"    └── Don Upgrade C  ↩ already shown\","""

for path in paths:
    text = path.read_text()
    for old, new in path_replacements:
        text = text.replace(old, new)
    text = text.replace(old_tree, new_tree)
    text = text.replace(old_tuple_tree, new_tuple_tree)
    path.write_text(text)
