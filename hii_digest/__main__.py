"""Allow ``python -m hii_digest``."""

from hii_digest.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
