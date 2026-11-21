import random
import io
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile


def generate_captcha():
    """Generate CAPTCHA image with 4 random characters and return (BufferedInputFile, answer)."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    answer = "".join(random.choices(chars, k=4))

    img = Image.new("RGB", (200, 80), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Add noise points
    for _ in range(200):
        x = random.randint(0, 199)
        y = random.randint(0, 79)
        draw.point((x, y), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))

    # Add noise lines
    for _ in range(5):
        start = (random.randint(0, 200), random.randint(0, 80))
        end = (random.randint(0, 200), random.randint(0, 80))
        draw.line([start, end], fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)), width=2)

    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()

    # Draw text with slight offset to make it harder for bots
    for i, ch in enumerate(answer):
        x = 30 + i * 40 + random.randint(-5, 5)
        y = 30 + random.randint(-5, 5)
        draw.text((x, y), ch, fill=(random.randint(0, 100), random.randint(0, 100), random.randint(0, 100)), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return BufferedInputFile(buf.getvalue(), filename="captcha.png"), answer