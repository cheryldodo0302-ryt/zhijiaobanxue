from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    output = Path(tempfile.gettempdir()) / "zhijiao-ingestion-smoke"
    output.mkdir(parents=True, exist_ok=True)
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    font = ImageFont.truetype(str(font_path), 42)
    formula_font = ImageFont.truetype(str(font_path), 54)
    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.text((100, 100), "数据库关系代数与函数依赖", fill="black", font=font)
    draw.text((100, 230), "选择 σ，投影 π，连接 ⋈", fill="black", font=font)
    draw.text((100, 370), "R(A, B, C)，A → B", fill="black", font=font)
    draw.text((100, 540), "E = mc²", fill="black", font=formula_font)
    image_path = output / "scanned_formula.png"
    pdf_path = output / "scanned_formula.pdf"
    image.save(image_path)
    image.save(pdf_path, "PDF", resolution=150)
    print(image_path)
    print(pdf_path)


if __name__ == "__main__":
    main()
