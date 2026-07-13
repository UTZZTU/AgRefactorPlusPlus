"""Allow ``python -m agrefactor`` to invoke the shared CLI."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
