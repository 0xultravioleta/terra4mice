"""Allow running terra4mice as ``python -m terra4mice``."""

from terra4mice.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
