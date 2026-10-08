# _setup: six files the remote tools would not write

These are the pre-commit config and the five GitHub workflow files. The bridge refuses to
write files that execute code (hooks run on your machine; workflows run on GitHub), so they
were placed here for you to read and move. From the repo root, in PowerShell:

    Move-Item _setup\.pre-commit-config.yaml .
    New-Item -ItemType Directory -Force .github\workflows | Out-Null
    Move-Item _setup\.github\workflows\*.yml .github\workflows\
    Remove-Item -Recurse -Force _setup
    Remove-Item .github\workflows\_probe.yml   # stray probe file; harmless but delete it

Then: uv sync ; uv run pre-commit install ; uv run pytest -q
