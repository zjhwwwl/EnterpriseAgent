"""Factory imports must not read a developer's invalid runtime configuration."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "overrides",
    [{"EA_PORT": "invalid"}, {"EA_LLM_ENABLED": "true", "EA_DEEPSEEK_API_KEY": ""}],
)
def test_factory_import_has_no_runtime_configuration_side_effects(tmp_path, overrides):
    environment = {**os.environ, **overrides}
    result = subprocess.run(
        [sys.executable, "-c", "from enterprise_agent.application import create_app"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
