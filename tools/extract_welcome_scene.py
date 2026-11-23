"""Extract the welcome artboard's red scenery without changing the PDF.

The original welcome layers and their measurements remain untouched. The new
manifest supplies the independent scenery and circle geometry used by the
responsive welcome composition; source drawing streams exist only in memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from extract_welcome_assets import SOURCE_SHA256, pdf_bytes, render

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'排版.pdf')
    parser.add_argument('--output', type=Path, default=ROOT/'app/assets/welcome')
    parser.add_argument('--reference-dir', type=Path, default=ROOT/'tmp/welcome-scene')
    args = parser.parse_args()
    digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise RuntimeError('Source PDF changed; verify the welcome paint ranges before extracting.')
    args.output.mkdir(parents=True, exist_ok=True)
    args.reference_dir.mkdir(parents=True, exist_ok=True)
    full = render(None, pdf_bytes(PdfReader(args.source), [(72, 78)]),
                  args.reference_dir/'scenery-full')
    bounds = full.getchannel('A').getbbox()
    if bounds is None:
        raise RuntimeError('The source red scenery layer is empty.')
    destination = args.output/'scene.png'
    full.crop(bounds).save(destination, optimize=True)
    data = {
        'schemaVersion': 1,
        'source': {'file': '排版.pdf', 'page': 1, 'sha256': digest, 'renderer': 'PDFium'},
        'artboard': [4096, 2160],
        'scenery': {'file': 'scene.png',
                    'sourceCrop': [bounds[0], bounds[1], bounds[2]-bounds[0], bounds[3]-bounds[1]],
                    'sourcePaintRanges': [[72, 78]],
                    'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                    'scaleMode': 'uniform-height-scale', 'anchor': 'horizontal-center',
                    'verticalAnchor': 'source-group-bottom-related-to-ui-source-bottom'},
        'circles': [
            {'center': [2780.5107, 1006.3225], 'radius': 761.753},
            {'center': [2780.5113, 1006.3225], 'radius': 568.414},
        ],
        'circleStroke': {'color': '#FF5C56', 'width': 3},
        'backgroundReference': '../workspace/background.json',
        'mapping': {
            'uiScale': 'min(viewportWidth / 4096, viewportHeight / 2160)',
            'sceneryScale': 'viewportHeight / 2160',
            'waveScale': 'viewportHeight / 2160',
            'waveInnerEdges': 'feather-overlaps-on-narrow-aspects-and-exposed-ends-on-wide-aspects',
            'structureAndBottomScale': 'uiScale',
            'anchors': {'brand_panel': 'bottom-left', 'mark': 'bottom-left',
                        'brand_text': 'bottom-left', 'start': 'bottom-right', 'next': 'top-right'},
            'layerOrder': ['paper', 'waves', 'circles', 'scenery', 'bottom', 'structure',
                           'brand_panel', 'mark', 'brand_text', 'start', 'next'],
            'wholePageLetterboxing': False,
            'nonUniformSceneryScaling': False,
            'pointerAndArtworkShareTransform': True,
        },
    }
    (args.output/'scene.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(destination), 'sourceCrop': data['scenery']['sourceCrop'],
                      'sha256': data['scenery']['sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
