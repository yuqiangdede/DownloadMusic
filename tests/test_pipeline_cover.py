import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline_core


class PipelineCoverTests(unittest.TestCase):
    @patch.object(pipeline_core, "fix_cover_image", return_value=True)
    @patch.object(pipeline_core, "is_image_decodable", return_value=False)
    @patch.object(pipeline_core, "extract_valid_image_bytes", return_value=(b"png", "image/png"))
    @patch.object(pipeline_core, "ID3")
    def test_apic_uses_ffmpeg_repair_when_strict_decode_fails(
        self, id3, _extract, _decode, repair
    ):
        id3.return_value.keys.return_value = ["APIC:Cover"]
        id3.return_value.__getitem__.return_value.data = b"png"
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "Cover.jpg"
            self.assertTrue(
                pipeline_core.extract_apic_to_jpg(Path("track.mp3"), output)
            )
            repair.assert_called_once_with("ffmpeg", output)

    def test_pillow_failure_falls_back_to_ffmpeg(self):
        fake_image = unittest.mock.MagicMock()
        opened = unittest.mock.MagicMock()
        opened.__enter__.return_value.verify.side_effect = ValueError("bad metadata")
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "Cover.jpg"
            image_path.write_bytes(b"not-empty")
            with (
                patch.object(pipeline_core, "Image", fake_image),
                patch.object(fake_image, "open", return_value=opened),
                patch.object(pipeline_core, "run_cmd_bytes", return_value=(0, b"", b"")) as run,
            ):
                self.assertTrue(
                    pipeline_core.is_image_decodable("ffmpeg", image_path)
                )
                run.assert_called_once()

    def test_png_signature_wins_over_jpg_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "Cover.jpg"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
            self.assertEqual(pipeline_core.detect_image_format(image_path), "png")


if __name__ == "__main__":
    unittest.main()
