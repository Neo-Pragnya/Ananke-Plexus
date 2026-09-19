"""Module entrypoint for `python -m ananke`."""

from ananke.plexus.cli.app import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
