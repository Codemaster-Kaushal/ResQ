from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "data" / "images"
root.mkdir(parents=True, exist_ok=True)


def make_img(name: str, bg, accent=(255, 255, 255), kind: str = "flood"):
    img = Image.new("RGB", (128, 128), bg)
    d = ImageDraw.Draw(img)

    if kind == "flood":
        d.rectangle((0, 60, 128, 128), fill=(30, 90, 180))
        d.rectangle((20, 20, 110, 70), fill=accent)
        d.line((20, 80, 110, 80), fill=(255, 255, 255), width=6)
    elif kind == "collapse":
        d.rectangle((10, 40, 118, 120), fill=(80, 80, 80))
        d.polygon([(20, 40), (64, 10), (108, 40), (90, 40), (90, 120), (38, 120), (38, 40)], fill=accent)
    elif kind == "infra":
        d.rectangle((0, 80, 128, 128), fill=(120, 120, 120))
        d.line((0, 60, 128, 60), fill=(255, 210, 80), width=8)
        d.line((20, 0, 20, 128), fill=(0, 0, 0), width=6)
        d.line((60, 0, 60, 128), fill=(0, 0, 0), width=6)
    elif kind == "water":
        d.rectangle((0, 70, 128, 128), fill=(80, 180, 255))
        d.rectangle((20, 20, 108, 80), fill=(200, 240, 255))
        d.line((64, 20, 64, 80), fill=(90, 90, 90), width=5)
    else:
        d.rectangle((0, 0, 128, 128), fill=accent)

    img.save(root / name)


make_img("flood_genuine.png", (180, 220, 255), kind="flood")
make_img("collapse.png", (150, 150, 150), accent=(220, 220, 220), kind="collapse")
make_img("infrastructure.png", (210, 210, 210), accent=(255, 200, 80), kind="infra")
make_img("waterlogging.png", (200, 235, 255), accent=(150, 200, 255), kind="water")

print("generated images:")
for path in sorted(root.iterdir()):
    print(path.name)
