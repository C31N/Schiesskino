from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tmp" / "menu_title_plaque_alpha.png"
OUTPUT = ROOT / "assets" / "arcade_themes" / "menu_title_sign_v1.png"
FONT = Path(r"C:\Windows\Fonts\DejaVuSerifCondensed-Bold.ttf")
TEXT = "SCHIEẞKINO"


def main() -> None:
    plaque = Image.open(SOURCE).convert("RGBA")
    bounds = plaque.getbbox()
    if bounds is None:
        raise ValueError("Leeres Titelschild")
    plaque = plaque.crop(bounds)
    plaque.thumbnail((620, 116), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", plaque.size, (0, 0, 0, 0))
    canvas.alpha_composite(plaque)
    draw = ImageDraw.Draw(canvas)
    font_size = 68
    font = ImageFont.truetype(str(FONT), font_size)
    while draw.textbbox((0, 0), TEXT, font=font, stroke_width=1)[2] > plaque.width - 94:
        font_size -= 1
        font = ImageFont.truetype(str(FONT), font_size)
    box = draw.textbbox((0, 0), TEXT, font=font, stroke_width=1)
    x = (plaque.width - (box[2] - box[0])) // 2 - box[0]
    y = (plaque.height - (box[3] - box[1])) // 2 - box[1] - 1

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.text((x + 2, y + 3), TEXT, font=font, fill=(0, 5, 8, 210), stroke_width=2, stroke_fill=(0, 3, 5, 230))
    shadow = shadow.filter(ImageFilter.GaussianBlur(1.2))
    canvas.alpha_composite(shadow)
    draw = ImageDraw.Draw(canvas)
    draw.text((x, y), TEXT, font=font, fill=(148, 134, 91, 255), stroke_width=2, stroke_fill=(21, 38, 39, 255))
    draw.text((x, y - 1), TEXT, font=font, fill=(162, 147, 99, 210), stroke_width=1, stroke_fill=(74, 88, 72, 230))
    canvas.save(OUTPUT, optimize=True)


if __name__ == "__main__":
    main()
