"""The fictional demo papers are photos the upload accepts, and they say they are fictional."""

from io import BytesIO

from PIL import Image

from app.demo_papers import lab_report_png, prescription_png
from app.domain.documents import MAX_IMAGE_BYTES, looks_like


def test_demo_papers_are_real_png_photos_small_enough_to_upload() -> None:
    for draw in (lab_report_png, prescription_png):
        photo = draw()
        assert looks_like("image/png", photo)
        assert len(photo) < MAX_IMAGE_BYTES
        with Image.open(BytesIO(photo)) as image:
            assert image.format == "PNG" and image.width == 1100 and image.height >= 800


def test_demo_papers_are_drawn_the_same_every_time() -> None:
    # The demo's earlier visit stores the prescription; a test compares it byte for byte.
    assert prescription_png() == prescription_png()
