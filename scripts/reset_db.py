"""Drop the schema and replay every migration — local dev convenience.

    uv run python -m scripts.reset_db

Destructive: it discards all uploaded documents. The deployed path is
`migrations/apply.sh`, which applies only what is missing and never drops.
"""

from app.storage import reset_schema


def main() -> None:
    versions = reset_schema()
    print(f"Schema reset. Applied: {', '.join(versions) or '(no migrations found)'}")


if __name__ == "__main__":
    main()
