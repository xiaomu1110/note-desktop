"""生成托盘图标 src/desktop_memo/assets/memo.ico（多尺寸），只在需要时手动跑一次。"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "desktop_memo" / "assets" / "memo.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    s = size * 8  # 先画大图再缩，边缘更干净
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    r = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=(255, 255, 255, 255))
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, outline=(228, 233, 241, 255),
                        width=max(1, int(s * 0.02)))

    # 顶部橙色圆点
    dot = int(s * 0.10)
    d.ellipse([int(s * 0.14), int(s * 0.13), int(s * 0.14) + dot, int(s * 0.13) + dot],
              fill=(240, 166, 60, 255))

    # 三条横线
    lw = max(2, int(s * 0.055))
    x0, x1 = int(s * 0.14), int(s * 0.86)
    for i, y in enumerate((0.38, 0.56, 0.74)):
        yy = int(s * y)
        xx = x1 if i != 2 else int(s * 0.62)
        d.rounded_rectangle([x0, yy, xx, yy + lw], radius=lw // 2,
                            fill=(118, 132, 152, 255))

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    imgs = [draw(s) for s in SIZES]
    imgs[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES],
                  append_images=imgs[:-1])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()