"""Point the test Home Assistant at this repository's custom integration."""

from pathlib import Path

import pytest


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir):
    config_dir = Path(hass_tmp_config_dir)
    components = Path(__file__).resolve().parents[2] / "custom_components"
    (config_dir / "custom_components").symlink_to(components, target_is_directory=True)
    return hass_tmp_config_dir
