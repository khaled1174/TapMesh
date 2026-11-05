#!/usr/bin/env python3
"""Black-box native helper qualification; developer test tools never ship in TapMesh."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class NativeToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = Path(os.environ['TAPMESH_TOOLS_TEST_BINARY']).resolve()
        cls.temp = tempfile.TemporaryDirectory(prefix='tapmesh-tools-')
        cls.root = Path(cls.temp.name)
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (1200, 400), 'white')
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 70)
        ImageDraw.Draw(image).text((100, 90), 'TapMesh 12345', font=font, fill='black')
        cls.arabic_image = cls.root / 'arabic-fixture.png'
        subprocess.run(['/usr/bin/swift', str(Path(__file__).with_name('make_arabic_fixture.swift')), str(cls.arabic_image)], check=True, timeout=45)
        cls.image = cls.root / 'fixture.png'
        image.save(cls.image)
        cls.video = cls.root / 'fixture.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=64x64:r=10:d=1', '-an', '-c:v', 'h264_videotoolbox',
                        '-y', str(cls.video)], check=True, timeout=15)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def invoke(self, *arguments, success=True):
        result = subprocess.run([str(self.binary), *map(str, arguments)], capture_output=True, timeout=45)
        self.assertLessEqual(len(result.stdout), 1024 * 1024)
        self.assertLessEqual(len(result.stderr), 4096)
        self.assertEqual(result.returncode == 0, success, result.stderr.decode())
        if success:
            self.assertEqual(result.stderr, b'')
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, b'')
        return json.loads(result.stderr)

    def test_capabilities_report_actual_english_and_arabic_only(self):
        result = self.invoke('ocr-capabilities')
        self.assertEqual(result['engine'], 'vision')
        self.assertIn('eng', result['languages'])
        self.assertEqual('ara' in result['languages'], any(x.split('-')[0] == 'ar' for x in result['supported_languages']))

    def test_english_recognition_scales_into_logical_top_left_coordinates(self):
        result = self.invoke('ocr', self.image, '--logical-width', '600', '--logical-height', '200', '--languages', 'eng')
        self.assertEqual(result['languages'], ['eng'])
        text = ' '.join(element['text'] for element in result['elements'])
        self.assertIn('TapMesh', text)
        self.assertIn('12345', text)
        for element in result['elements']:
            self.assertEqual(element['role'], 'text')
            self.assertEqual(element['source'], 'ocr')
            bounds = element['bounds']
            self.assertGreater(bounds['x'], 35)
            self.assertLess(bounds['y'], 85)
            self.assertGreater(bounds['y'], 30)
            self.assertLessEqual(bounds['x'] + bounds['width'], 600)
            self.assertLessEqual(bounds['y'] + bounds['height'], 200)

    def test_arabic_recognition_when_system_advertises_support(self):
        capabilities = self.invoke('ocr-capabilities')
        if 'ara' not in capabilities['languages']:
            self.skipTest('This macOS Vision does not advertise Arabic recognition')
        result = self.invoke('ocr', self.arabic_image, '--logical-width', '600', '--logical-height', '200', '--languages', 'ara')
        text = ' '.join(element['text'] for element in result['elements'])
        self.assertIn('مرحبا', text)
        self.assertIn('تجربة', text)
        self.assertEqual(result['languages'], ['ara'])

    def test_combined_languages_retain_arabic_recognition(self):
        if 'ara' not in self.invoke('ocr-capabilities')['languages']:
            self.skipTest('This macOS Vision does not advertise Arabic recognition')
        result = self.invoke('ocr', self.arabic_image, '--logical-width', '600', '--logical-height', '200', '--languages', 'eng+ara')
        self.assertEqual(result['languages'], ['eng', 'ara'])
        self.assertIn('تجربة', ' '.join(element['text'] for element in result['elements']))

    def test_probe_finalized_video(self):
        result = self.invoke('probe', self.video)
        self.assertAlmostEqual(result['duration_seconds'], 1.0, delta=.15)
        self.assertEqual(result['video_tracks'], 1)

    def test_probe_invalid_file_has_safe_error(self):
        private_path = self.root / 'secret-private-name.txt'
        private_path.write_text('SECRET SENTINEL VALUE')
        result = self.invoke('probe', private_path, success=False)
        self.assertNotIn('secret-private-name', json.dumps(result))
        self.assertNotIn('SENTINEL', json.dumps(result))

    def test_invalid_dimensions_are_rejected(self):
        result = self.invoke('ocr', self.image, '--logical-width', 'NaN', '--logical-height', '200', '--languages', 'eng', success=False)
        self.assertEqual(result['error']['code'], 'invalid_arguments')

    def test_unknown_language_is_rejected(self):
        result = self.invoke('ocr', self.image, '--logical-width', '600', '--logical-height', '200', '--languages', 'fra', success=False)
        self.assertEqual(result['error']['code'], 'unsupported_language')

    def test_aspect_mismatch_is_rejected(self):
        result = self.invoke('ocr', self.image, '--logical-width', '200', '--logical-height', '600', '--languages', 'eng', success=False)
        self.assertEqual(result['error']['code'], 'invalid_arguments')

    def test_invalid_image_has_safe_error(self):
        path = self.root / 'invalid-private-name.png'
        path.write_text('NOT AN IMAGE')
        result = self.invoke('ocr', path, '--logical-width', '600', '--logical-height', '200', '--languages', 'eng', success=False)
        self.assertEqual(result['error']['code'], 'invalid_image')
        self.assertNotIn('invalid-private-name', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
