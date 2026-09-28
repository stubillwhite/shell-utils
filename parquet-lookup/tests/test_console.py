import pytest

from parquet_lookup.app import main


def test_cli_requires_a_command() -> None:
    with pytest.raises(SystemExit) as error:
        main([])

    assert error.value.code == 2
