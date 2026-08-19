from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "assets" / "arcade_themes" / "menu_background_v3.png"
EDITED = ROOT / "tmp" / "menu_background_hole_edit.png"
OUTPUT = ROOT / "assets" / "arcade_themes" / "menu_background_v4.png"


def main() -> None:
    original = Image.open(ORIGINAL).convert("RGB")
    edited = Image.open(EDITED).convert("RGB")
    if original.size != edited.size:
        raise ValueError("Hintergrund und Lochentwurf besitzen verschiedene Größen")

    # Nur der unmittelbar bearbeitete Schrank-/Bodenbereich wird übernommen.
    # Eine weiche, eng geführte Maske lässt Holzmaserung und Beleuchtung des
    # unveränderten V3-Hintergrunds außerhalb des Lochs pixelgenau bestehen.
    mask = Image.new("L", original.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(
        ((184, 971), (195, 958), (214, 960), (229, 977),
         (230, 1017), (220, 1033), (190, 1033), (181, 1017)),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(2.0))
    Image.composite(edited, original, mask).save(OUTPUT, optimize=True)


if __name__ == "__main__":
    main()
