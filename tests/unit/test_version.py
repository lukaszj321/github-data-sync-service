from __future__ import annotations

from importlib.metadata import version

import pytest

import github_data_sync_service
from github_data_sync_service.worker.main import build_parser


def test_package_version() -> None:
    assert github_data_sync_service.__version__ == "0.2.0"
    assert version("github-data-sync-service") == "0.2.0"


def test_worker_version_output(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == "github-data-sync-worker 0.2.0"
