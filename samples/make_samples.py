"""Draws the sample prescription images in this folder.

Everything on them is made up: the clinic, the doctors and the patients do not exist.
Run with: python samples/make_samples.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent

SAMPLES = {
    "sample_1_simple.png": {
        "doctor": "Dr. Test Doctor",
        "patient": "Asha Example",
        "date": "03 Oct 2026",
        "lines": [
            "1. Tab. Metformin 500 mg",
            "   1 tablet, morning and night, after food",
            "   for 3 days",
            "",
            "2. Tab. Amlodipine 5 mg",
            "   1 tablet, morning",
            "   for 3 days",
        ],
    },
    "sample_2_pattern.png": {
        "doctor": "Dr. Sample Physician",
        "patient": "Ravi Placeholder",
        "date": "03 Oct 2026",
        "lines": [
            "1. Cap. Amoxicillin 500 mg      1-1-1      5 days",
            "   after food",
            "",
            "2. Tab. Paracetamol 650 mg      1-0-1      3 days",
            "   after food, if fever",
            "",
            "3. Cap. Omeprazole 20 mg        1-0-0      5 days",
            "   before food",
        ],
    },
    "sample_3_incomplete.png": {
        "doctor": "Dr. Demo Practitioner",
        "patient": "Maria Sample",
        "date": "03 Oct 2026",
        "lines": [
            "1. Tab. Atorvastatin 10 mg",
            "   1 tablet at night",
            "   for 7 days",
            "",
            "2. Syr. Cetirizine",
            "   5 ml twice daily",
            "",
            "3. Tab. Vitamin D3 1000 IU",
            "   1 tablet in the morning, after food",
        ],
    },
}


def font(size: int, bold: bool = False):
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def draw(sample: dict) -> Image.Image:
    image = Image.new("RGB", (1100, 1000), "white")
    pen = ImageDraw.Draw(image)
    pen.text((60, 50), "SAMPLE CLINIC (NOT REAL)", font=font(40, bold=True), fill="black")
    pen.text((60, 105), "1 Example Street, Testville", font=font(24), fill="black")
    pen.text((60, 140), sample["doctor"], font=font(26), fill="black")
    pen.line((60, 190, 1040, 190), fill="black", width=3)
    pen.text((60, 215), f"Patient: {sample['patient']}", font=font(28), fill="black")
    pen.text((760, 215), f"Date: {sample['date']}", font=font(28), fill="black")
    pen.text((60, 285), "Rx", font=font(54, bold=True), fill="black")
    y = 380
    for line in sample["lines"]:
        pen.text((90, y), line, font=font(30), fill="black")
        y += 48
    pen.text((60, 930), "Made-up sample for software testing. Not a real prescription.", font=font(20), fill="gray")
    return image


if __name__ == "__main__":
    for name, sample in SAMPLES.items():
        draw(sample).save(OUT / name)
        print(f"wrote {name}")
