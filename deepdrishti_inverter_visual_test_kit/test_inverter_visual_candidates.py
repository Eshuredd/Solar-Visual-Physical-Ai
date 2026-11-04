import csv
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from inverter_visual_candidates import analyze_rgb_pair, analyze_thermal_measurements, annotate_rgb


def _image_pair(tmp_path: Path, *, change: bool) -> tuple[Path, Path]:
    ref = Image.new('RGB', (100, 80), (80, 100, 120))
    curr = ref.copy()
    if change:
        draw = ImageDraw.Draw(curr)
        draw.rectangle([20, 30, 45, 52], fill=(235, 220, 35))
    ref_path, cur_path = tmp_path/'reference.png', tmp_path/'current.png'
    ref.save(ref_path); curr.save(cur_path)
    return ref_path, cur_path


def _csv(path: Path, arr):
    with path.open('w', newline='') as f:
        writer=csv.writer(f); writer.writerows(arr)
    return path


def test_no_visible_change_returns_empty(tmp_path):
    reference, current = _image_pair(tmp_path, change=False)
    result = analyze_rgb_pair(reference, current)
    assert result['status'] == 'no_change_detected'
    assert result['candidate_regions'] == []


def test_rgb_regions_are_localized_but_unclassified(tmp_path):
    reference, current = _image_pair(tmp_path, change=True)
    result = analyze_rgb_pair(reference, current)
    assert result['status'] == 'candidate_regions'
    found = result['candidate_regions']
    assert len(found) == 1
    x0,y0,x1,y1=found[0]['bbox_xyxy']
    assert x0 <= 20 and x1 >= 45 and y0 <= 30 and y1 >= 52
    assert found[0]['candidate_type'] == 'visual_change_unclassified'
    out=tmp_path/'marked.png'
    annotate_rgb(current,result,out)
    assert out.exists()


def test_thermal_numeric_change_not_rgb_palette(tmp_path):
    ref = [[30.0]*20 for _ in range(16)]
    cur = [[30.0]*20 for _ in range(16)]
    for y in range(4,9):
        for x in range(7,12):
            cur[y][x]=44.0
    ref_path=_csv(tmp_path/'ref.csv',ref)
    cur_path=_csv(tmp_path/'cur.csv',cur)
    result=analyze_thermal_measurements(ref_path,cur_path,delta_threshold_c=10.0)
    assert result['status']=='candidate_regions'
    assert len(result['candidate_regions'])==1
    assert result['candidate_regions'][0]['peak_temperature_rise_c']==14.0
    assert result['candidate_regions'][0]['bbox_xyxy']==[7,4,12,9]


def test_unmatched_view_is_rejected(tmp_path):
    reference, current = _image_pair(tmp_path, change=False)
    Image.new('RGB', (120,80)).save(current)
    with pytest.raises(ValueError, match='same dimensions'):
        analyze_rgb_pair(reference,current)


def test_malformed_thermal_rows_are_rejected(tmp_path):
    reference=_csv(tmp_path/'ref.csv',[[25.0,25.0],[25.0,25.0]])
    current=_csv(tmp_path/'current.csv',[[25.0,25.0],[25.0]])
    with pytest.raises(ValueError, match='rectangular'):
        analyze_thermal_measurements(reference,current)
