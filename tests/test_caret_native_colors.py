"""Calibrate the color calculation against independently rendered native bytes."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent.text_frame_guard import _blue_antialias_transition


@pytest.mark.skipif(sys.platform != 'darwin', reason='CoreGraphics calibration requires macOS')
def test_native_blue_compositing_rounds_into_the_accepted_color_model(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'macos/Tests/TapMeshToolsTests/make_caret_colors.swift'
    output = tmp_path / 'native-caret-colors.json'
    result = subprocess.run(['swift', str(source), str(output)], capture_output=True,
                            text=True, timeout=45)
    assert result.returncode == 0, result.stderr[:2000]
    samples = json.loads(output.read_text())
    assert len(samples) == 2560
    rejected = []
    for sample in samples:
        background, pixel = tuple(sample['background']), tuple(sample['pixel'])
        assert sample['background'] == sample['requested_background']
        if not (_blue_antialias_transition(background, pixel)
                or _blue_antialias_transition(pixel, background)):
            rejected.append(sample)
    assert not rejected, {'rejected': len(rejected), 'examples': rejected[:5]}
