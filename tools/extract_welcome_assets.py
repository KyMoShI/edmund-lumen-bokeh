"""Extract the approved first PDF artboard into composable welcome assets.

Source content and layout remain unchanged. Only temporary, in-memory PDF
streams are created for rendering individual paint operations. PDFium is the
transparent renderer fallback when Poppler's pdftocairo is unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageChops
import numpy as np
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject

ROOT = Path(__file__).resolve().parents[1]
ARTBOARD = (4096, 2160)
# Indices refer to the approved PDF's first-page content stream. Rendering
# keeps all graphics-state / transform / clip operators and filters paints.
PAINT = {b'f', b'F', b'f*', b'S', b's', b'B', b'B*', b'b', b'b*',
         b'Do', b'sh', b'Tj', b'TJ', b"'", b'"'}
PATH_PAINT = {b'f', b'F', b'f*', b'S', b's', b'B', b'B*', b'b', b'b*'}
LAYERS = {
    'background': [(0, 123)],
    'brand_panel': [(129, 129), (133, 133), (493, 506)],
    'mark': [(139, 139)],
    'brand_text': [(143, 143), (150, 492)],
    'start': [(507, 534), (540, 657)],
    'next': [(658, 748)],
}
SOURCE_SHA256 = '945627123d706f889935ad8ccfc88589b9c85ad7171ea7a5737bd5c2d3c6591e'


def find_poppler(value):
    if value:
        candidate = Path(value)
        if candidate.is_dir():
            candidate /= 'pdftocairo.exe' if sys.platform == 'win32' else 'pdftocairo'
        if candidate.is_file():
            return str(candidate)
        raise FileNotFoundError(candidate)
    found = shutil.which('pdftocairo')
    if found:
        return found
    bundled = Path(sys.executable).resolve().parents[1] / 'native/poppler/Library/bin/pdftocairo.exe'
    if bundled.is_file():
        return str(bundled)
    return None


def pdf_bytes(reader, selected=None, *, omit_strokes=False):
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    source = ContentStream(reader.pages[0]['/Contents'], reader)
    if selected is not None:
        operations = []
        for index, (args, operator) in enumerate(source.operations):
            keep = any(start <= index <= end for start, end in selected)
            if (operator in PAINT and not keep) or (omit_strokes and operator in (b'S',b's')):
                if operator in PATH_PAINT:
                    operations.append(([], b'n'))
                continue
            operations.append((args, operator))
        target = ContentStream(None, writer)
        target.operations = operations
        page[NameObject('/Contents')] = writer._add_object(target)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def render(poppler, data, output):
    if poppler is None:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(data)
        page = document[0]
        bitmap = page.render(scale=1, fill_color=(0, 0, 0, 0))
        image = bitmap.to_pil().convert('RGBA').copy()
        image.save(output.with_suffix('.png'))
        bitmap.close(); page.close(); document.close()
        return image
    # Reading the temporary stream from stdin preserves the source PDF file.
    subprocess.run([poppler, '-png', '-transp', '-singlefile', '-r', '72', '-', str(output)],
                   input=data, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    path = output.with_suffix('.png')
    with Image.open(path) as image:
        return image.convert('RGBA')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / '排版.pdf')
    parser.add_argument('--output', type=Path, default=ROOT / 'app/assets/welcome')
    parser.add_argument('--reference-dir', type=Path, default=ROOT / 'tmp/welcome-assets')
    parser.add_argument('--poppler')
    args = parser.parse_args()
    digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise RuntimeError('The source PDF changed; re-check paint ranges before extracting.')
    poppler = find_poppler(args.poppler)
    args.output.mkdir(parents=True, exist_ok=True)
    args.reference_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(args.source)
    original = render(poppler, pdf_bytes(reader), args.reference_dir / 'reference')
    assert original.size == ARTBOARD
    # Source page includes its own white paper and red left strip.
    composite = Image.new('RGBA', ARTBOARD, (0, 0, 0, 0))
    layers = []
    for name, selected in LAYERS.items():
        full = render(poppler, pdf_bytes(reader, selected, omit_strokes=name in ('start','next')), args.reference_dir / f'{name}-full')
        bounds = (0, 0, *ARTBOARD) if name == 'background' else full.getchannel('A').getbbox()
        if bounds is None:
            raise RuntimeError(f'Empty layer: {name}')
        crop = full.crop(bounds)
        crop.save(args.output / f'{name}.png', optimize=True)
        composite.alpha_composite(crop, (bounds[0], bounds[1]))
        layers.append({'name': name, 'file': f'{name}.png',
                       'rect': [bounds[0], bounds[1], bounds[2]-bounds[0], bounds[3]-bounds[1]]})
    composite.save(args.reference_dir / 'composite.png')
    difference = np.abs(np.asarray(original).astype(np.int16)-np.asarray(composite).astype(np.int16))
    # Rendered glyph/pupil alpha quantization can differ by one or two levels
    # when independent transparent layers are recomposed.
    summary = {'maxChannelDifference': int(difference.max()),
               'meanAbsoluteChannelDifference': float(difference.mean()),
               'changedPixels': int(np.any(difference, axis=2).sum())}
    ImageChops.difference(original, composite).save(args.reference_dir / 'difference.png')
    layout = {
        'schemaVersion': 1,
        'artboard': list(ARTBOARD),
        'coordinateSystem': 'top-left source design points; 1 source point = 1 asset pixel',
        'source': {'file': '排版.pdf', 'page': 1, 'sha256': digest,
                   'renderer': 'Poppler pdftocairo' if poppler else 'PDFium'},
        'layers': layers,
        'buttons': {
            'start': {'rect': [2859.5669,1595.3275,901.923,259.98], 'radius':50,
                      'layer':'start', 'accessibleName':'START', 'action':'start'},
            'next': {'rect':[3310.529,218.26,463.558,126.005], 'radius':0,
                     'layer':'next', 'accessibleName':'NEXT', 'action':'resume'},
        },
        'brandText': {
            'layer':'brand_text', 'text':'伯爵大光圈',
            'font':{'family':'Sarasa Gothic SC','weight':700,'sourceSize':248.8141},
            'glyphRightEdges':[667.8483,916.6624,1165.4765,1414.2906,1663.1047],
            'note':'Reveal includes original white glyph fill and blue vector hairline outline.'
        },
        'verification':summary,
        'runtimeStyle':{'actionText':'white-fill-without-blue-outline','brandText':'source-artwork'},
    }
    (args.output / 'layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'layers':layers,'verification':summary},ensure_ascii=False))


if __name__ == '__main__':
    main()
