"""Console entrypoint installed as the acpbox script (see pyproject.toml)."""


def main() -> None:
    from acpbox.main import run

    run()
