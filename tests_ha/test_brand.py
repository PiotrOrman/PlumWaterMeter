"""Brand images shipped with the integration (custom_components/ewebtel/brand)."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from PIL import Image

BRAND_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "ewebtel" / "brand"


@pytest.mark.parametrize(
    ("name", "size"),
    [("icon.png", (256, 256)), ("icon@2x.png", (512, 512))],
)
def test_icons_are_square(name: str, size: tuple[int, int]) -> None:
    with Image.open(BRAND_DIR / name) as image:
        assert image.size == size
        assert image.mode == "RGBA"
        # Transparent corners: no background box behind the glyph.
        assert image.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize(
    ("name", "height"),
    [
        ("logo.png", 128),
        ("logo@2x.png", 256),
        ("dark_logo.png", 128),
        ("dark_logo@2x.png", 256),
    ],
)
def test_logos_fit_the_brand_rules(name: str, height: int) -> None:
    # Brand rules: logo shortest side 128-256 px, @2x 256-512 px; trimmed.
    with Image.open(BRAND_DIR / name) as image:
        assert image.height == height
        assert image.width > image.height
        assert image.getbbox() == (0, 0, image.width, image.height)


@pytest.mark.parametrize(
    ("requested", "served_file"),
    [
        ("icon.png", "icon.png"),
        ("icon@2x.png", "icon@2x.png"),
        ("logo.png", "logo.png"),
        ("dark_logo.png", "dark_logo.png"),
        # No dark icon on purpose: the purple icon works on dark themes too.
        ("dark_icon.png", "icon.png"),
    ],
)
async def test_home_assistant_serves_the_brand_images(
    hass: HomeAssistant, hass_client, requested: str, served_file: str
) -> None:
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    response = await client.get(f"/api/brands/integration/ewebtel/{requested}")
    assert response.status == 200
    assert await response.read() == (BRAND_DIR / served_file).read_bytes()
