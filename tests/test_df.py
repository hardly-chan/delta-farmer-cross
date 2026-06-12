from pathlib import Path

import pytest

import df

ROOT = Path(__file__).resolve().parents[1]


def test_common_command_maps_to_exchange_app():
    assert df.app_argv(["trade", "omni"]) == [str(ROOT / "apps/omni.py"), "trade"]


def test_common_command_passes_arguments():
    assert df.app_argv(["stats", "omni", "this", "--force"]) == [
        str(ROOT / "apps/omni.py"),
        "stats",
        "this",
        "--force",
    ]


def test_setup_maps_to_config_new():
    assert df.app_argv(["setup", "hyena"]) == [str(ROOT / "apps/hyena.py"), "config", "new"]


def test_config_maps_to_config_subcommand():
    assert df.app_argv(["config", "nado", "encrypt", "-c", "my.toml"]) == [
        str(ROOT / "apps/nado.py"),
        "config",
        "encrypt",
        "-c",
        "my.toml",
    ]


def test_tool_maps_to_exchange_specific_command():
    assert df.app_argv(["tool", "hyena", "reward", "claim"]) == [
        str(ROOT / "apps/hyena.py"),
        "reward",
        "claim",
    ]


def test_unknown_exchange_exits():
    with pytest.raises(SystemExit):
        df.app_argv(["trade", "missing"])


def test_unknown_tool_exits():
    with pytest.raises(SystemExit):
        df.app_argv(["tool", "omni", "reward", "claim"])
