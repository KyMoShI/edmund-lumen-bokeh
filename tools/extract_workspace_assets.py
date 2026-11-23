"""Extract static artwork and exact anchors from the approved workspace artboard.

The source PDF remains read-only. Temporary PDF streams are held in memory to
render selected paint operations; the application consumes only PNG and JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = '945627123d706f889935ad8ccfc88589b9c85ad7171ea7a5737bd5c2d3c6591e'
ARTBOARD = (4096, 2160)
PAINT = {b'f', b'F', b'f*', b'S', b's', b'B', b'B*', b'b', b'b*',
         b'Do', b'sh', b'Tj', b'TJ', b"'", b'"'}
PATH_PAINT = {b'f', b'F', b'f*', b'S', b's', b'B', b'B*', b'b', b'b*'}
LAYERS = {
    'background': [(0, 85), (1497, 1499)],
    'preview_panel': [(161, 194), (682, 711)],
    'history_panel': [(127, 160), (652, 681)],
    'parameters_panel': [(86, 126), (603, 611), (632, 651)],
    'parameters_pattern': [(583, 598)],
    'camera': [(1490, 1496)],
    'metadata_frame': [(1335, 1340)],
    'import': [(401, 562)],
    'export': [(224, 400)],
    'tag_original': [(1514, 1521)],
    'tag_effect': [(1500, 1509)],
    'tag_depth': [(1526, 1533)],
}

# Separate responsive background geometry from the original reference bitmap.
# Paint indices are page-3 content-stream indices, with all original clips and
# graphics-state operations retained by selected_pdf().
BACKGROUND_LAYERS = {
    'left-band': [(69, 71)],
    'guides': [(72, 85)],
    'waves': [(6, 45)],
    'squares': [(46, 50)],
    'bottom': [(51, 68)],
}


def selected_pdf(reader, ranges=None, *, omit_strokes=False):
    writer = PdfWriter()
    page = writer.add_page(reader.pages[2])
    if ranges is not None:
        original = ContentStream(reader.pages[2]['/Contents'], reader)
        operations = []
        for index, (args, operator) in enumerate(original.operations):
            keep = any(first <= index <= last for first, last in ranges)
            if (operator in PAINT and not keep) or (omit_strokes and operator in (b'S',b's')):
                if operator in PATH_PAINT:
                    operations.append(([], b'n'))
                continue
            operations.append((args, operator))
        stream = ContentStream(None, writer)
        stream.operations = operations
        page[NameObject('/Contents')] = writer._add_object(stream)
    memory = io.BytesIO()
    writer.write(memory)
    return memory.getvalue()


def render(data):
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(data)
    page = document[0]
    bitmap = page.render(scale=1, fill_color=(0, 0, 0, 0))
    image = bitmap.to_pil().convert('RGBA').copy()
    bitmap.close(); page.close(); document.close()
    return image


def rectangles():
    """All anchors use source-artboard top-left coordinates in design units."""
    return {
        'panels': {
            'preview': {'rect': [301.2347, 207.2003, 2098.321, 1816.133], 'radius': 50, 'stroke': 2, 'layer': 'preview_panel'},
            'history': {'rect': [2464.3891, 207.2011, 405.097, 1302.910], 'radius': 50, 'stroke': 2, 'layer': 'history_panel'},
            'parameters': {'rect': [2906.1663, 207.2010, 933.417, 1602.799], 'radius': 50, 'stroke': 2, 'layer': 'parameters_panel'},
        },
        'previews': {
            'original': {'rect': [384.785, 283.243, 1014.826, 1659.979], 'tagRect': [384.785, 283.244, 253.341, 73.026], 'tagLayer': 'tag_original'},
            'effect': {'rect': [1427.451, 283.243, 905.493, 843.979], 'tagRect': [1427.452, 283.244, 253.341, 73.026], 'tagLayer': 'tag_effect'},
            'depth': {'rect': [1428.945, 1165.403, 902.507, 777.819], 'tagRect': [1428.944, 1165.403, 253.341, 73.026], 'tagLayer': 'tag_depth'},
        },
        'buttons': {
            'import': {'rect': [2551.9016, 1878.0002, 506.682, 146.051], 'radius': 50, 'layer': 'import', 'accessibleName': 'IMPORT'},
            'export': {'rect': [3332.9009, 1878.0002, 506.682, 146.051], 'radius': 50, 'layer': 'export', 'accessibleName': 'EXPORT'},
        },
        'metadata': {
            'frameRect': [2957.753, 352.094, 798.826, 302.132],
            'imageRect': [2960.753, 355.094, 792.826, 296.133],
            'visibleImageRect': [2960.753, 355.094, 792.826, 276.199],
            'infoBarRect': [2957.753, 631.293, 798.826, 59.264],
            'frameLayer': 'metadata_frame',
            'note': 'The blue info bar overlays the lower part of the image frame in the source.',
        },
        'history': {
            'titleRect': [2506.753, 283.244, 324.969, 73.026],
            'listRect': [2506.753, 372.015, 324.969, 370.985],
            'settingsTitleRect': [2540.2668, 822.0848, 253.341, 64.262],
            'themeRect': [2506.7535, 923.8707, 324.969, 56.665],
            'motionRect': [2506.7535, 1008.6005, 324.969, 56.665],
            'helpRect': [2506.7535, 1093.3302, 324.969, 56.665],
            'rangeIndicatorRect': [2561.888, 1195.9257, 24.51, 24.51],
            'rangeLabelOrigin': [2596.5879, 1219.7217],
            'qualityLabelRect': [2540.2668, 1284.3483, 130, 37.128],
            'qualityControlRect': [2687.0442, 1284.3483, 109.306, 37.128],
            'reportRect': [2540.2668, 1345.6604, 253.341, 73.026],
        },
        'parameters': {
            'lensTitleRect': [3010.2773, 793.1113, 671.36, 64.262],
            'lensRuleRect': [2957.753, 874.744, 798.826, 2.2],
            'focusTitleRect': [3010.2773, 1391.2796, 671.36, 64.262],
            'focusRuleRect': [2957.753, 1472.913, 798.826, 2.2],
            'labelX': 3018.8857,
            'controlX': 3187.3951,
            'valueX': 3626.1423,
            'dropdownWidth': 491.883,
            'dropdownHeight': 56.665,
            'dropdownRadius': 14.242,
            'sliderTrackX': 3187.3949,
            'sliderTrackWidth': 419.144,
            'sliderTrackHeight': 28.484,
            'sliderHandleSize': [28.484, 56.665],
            'rowPitch': 91.0582,
            'apertureRect': [3187.3951, 923.8707, 491.883, 56.665],
            'blurTrackRect': [3187.3949, 1029.0189, 419.144, 28.484],
            'strengthTrackRect': [3187.3949, 1120.0772, 419.144, 28.484],
            'densityTrackRect': [3187.3949, 1211.1363, 419.144, 28.483],
            'sizeTrackRect': [3187.3949, 1302.1945, 419.144, 28.483],
            'focusModeRect': [3187.3951, 1502.0531, 491.883, 56.665],
            'modelRect': [3187.3951, 1583.5975, 491.883, 56.665],
            'scrollTrackRect': [3791.8889, 341.6664, 31.094, 1427.667],
            'scrollThumbRect': [3791.8889, 423.4108, 31.094, 442.995],
        },
        'footer': {'rect': [0, 2109.889, 4096, 50.111], 'versionTextRight': 4089.0431},
        'typography': {'family': 'Sarasa Gothic SC', 'control': 35.7778, 'section': 44.7222, 'previewTag': 42.5, 'metadata': 32, 'caption': 30, 'action': 85},
    }


def extract_background(reader, output, digest):
    """Retain source artwork while recording per-layer anchoring semantics."""
    images = {name: render(selected_pdf(reader, ranges))
              for name, ranges in BACKGROUND_LAYERS.items()}
    specs = [
        ('left-band', 'left-band', None, 'left-full-height', 'fixed-logical-width-extend-height'),
        ('guides', 'guides', None, 'top-left-and-top-right-inset', 'fixed-stroke-and-top-offset-extend-horizontal-line'),
        ('wave-left', 'waves', (0, 0, 2048, 2160), 'left-content-edge-and-top', 'uniform-height-scale-inner-crop'),
        ('wave-right', 'waves', (2048, 0, 4096, 2160), 'right-and-top', 'uniform-height-scale-inner-crop'),
        ('squares', 'squares', None, 'top-right', 'fixed-logical-size'),
        ('bottom', 'bottom', None, 'bottom-left-and-bottom-right', 'three-slice-fixed-height-and-slope-extend-middle'),
    ]
    layers = []
    for name, group, selection, anchor, scale_mode in specs:
        full = images[group]
        if selection is not None:
            sub = full.crop(selection)
            local = sub.getchannel('A').getbbox()
            if local is None:
                raise RuntimeError(f'Empty background crop: {name}')
            bounds = (selection[0]+local[0], selection[1]+local[1],
                      selection[0]+local[2], selection[1]+local[3])
        else:
            bounds = full.getchannel('A').getbbox()
        if bounds is None:
            raise RuntimeError(f'Empty background layer: {name}')
        file = f'background-{name}.png'
        path = output/file
        full.crop(bounds).save(path, optimize=True)
        layers.append({'name': name, 'file': file,
                       'sourceCrop': [bounds[0], bounds[1], bounds[2]-bounds[0], bounds[3]-bounds[1]],
                       'sourcePaintRanges': [list(pair) for pair in BACKGROUND_LAYERS[group]],
                       'anchor': anchor, 'scaleMode': scale_mode,
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    data = {
        'schemaVersion': 1,
        'source': {'file': '排版.pdf', 'page': 3, 'sha256': digest, 'renderer': 'PDFium'},
        'coordinateSystem': 'top-left source design units; source PNGs are 1 pixel per design unit',
        'artboard': list(ARTBOARD),
        'referenceScale': 1/3,
        'referenceLogicalSize': [4096/3, 720],
        'paperColor': '#FFFFFF',
        'layers': layers,
        'geometry': {
            'leftBand': {'width': 30.307, 'color': '#FF3D36'},
            'guides': {'strokeWidth': 3, 'color': '#FF5C56',
                       'horizontal': {'left': 144.3077, 'top': 130.0513, 'rightInset': 559.6923},
                       'vertical': {'left': 210.9744, 'top': 72.0513, 'length': 544}},
            'squares': {'color': '#001EFF', 'size': 37.065,
                        'top': 111.519, 'rightInsets': [175.477, 115.477]},
            'waves': {'sourceSplitX': 2048, 'scaleReference': 'viewport-height / 2160',
                      'leftContentInset': 30.307, 'innerEdgeFadeDesignWidth': 192,
                      'innerEdgeFadeGapThresholdLogical': 2,
                      'overlapPolicy': 'clip-left-and-right-artwork-to-their-viewport-halves'},
            'bottom': {'sourceSliceX': [268, 3789], 'colorDirection': 'vertical',
                       'sourcePolygon': [[267.639, 1885.857], [0, 2154.428], [0, 2160],
                                         [4096, 2160], [3789.748, 1885.857]],
                       'preserveFixedLogicalCapWidths': True,
                       'preserveFixedLogicalColorBandHeight': True},
            'footer': {'drawnBy': 'workspace', 'sourceRect': [0, 2109.889, 4096, 50.111]},
        },
        'rendering': {'baseAndGuideGeometry': 'vector-from-recorded-source-values',
                      'wavesAndGradient': 'source-raster-layer-sampling',
                      'wholeBackgroundNonUniformScaling': False,
                      'viewportOriginSupported': True,
                      'footerAndVersionExcluded': True},
        'referenceComparison': {'expected': 'source background apart from footer at referenceLogicalSize',
                                'allowance': 'subpixel raster filtering and independently antialiased vector edges'},
    }
    (output/'background.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'排版.pdf')
    parser.add_argument('--output', type=Path, default=ROOT/'app/assets/workspace')
    parser.add_argument('--reference-dir', type=Path, default=ROOT/'tmp/workspace-assets')
    parser.add_argument('--background-only', action='store_true', help='Update only responsive background PNGs and background.json.')
    args = parser.parse_args()
    digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise RuntimeError('The source PDF changed; re-check the page-3 paint ranges first.')
    args.output.mkdir(parents=True, exist_ok=True)
    args.reference_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(args.source)
    background = extract_background(reader, args.output, digest)
    if args.background_only:
        print(json.dumps({'output': str(args.output), 'backgroundLayers': background['layers']}, ensure_ascii=False))
        return
    original = render(selected_pdf(reader))
    assert original.size == ARTBOARD
    original.save(args.reference_dir/'reference.png')
    original.resize((1366,720),Image.Resampling.LANCZOS).save(args.reference_dir/'reference-preview.png')
    composite = Image.new('RGBA',ARTBOARD,(0,0,0,0))
    layers=[]
    for name,ranges in LAYERS.items():
        image=render(selected_pdf(reader,ranges,omit_strokes=name in ('import','export')))
        bounds=(0,0,*ARTBOARD) if name=='background' else image.getchannel('A').getbbox()
        if bounds is None:
            raise RuntimeError(f'Empty source artwork: {name}')
        crop=image.crop(bounds)
        crop.save(args.output/f'{name}.png',optimize=True)
        composite.alpha_composite(crop,(bounds[0],bounds[1]))
        layers.append({'name':name,'file':f'{name}.png','rect':[bounds[0],bounds[1],bounds[2]-bounds[0],bounds[3]-bounds[1]]})
    composite.save(args.reference_dir/'static-composite.png')
    composite.resize((1366,720),Image.Resampling.LANCZOS).save(args.reference_dir/'static-preview.png')
    layout={'schemaVersion':1,'artboard':list(ARTBOARD),'source':{'file':'排版.pdf','page':3,'sha256':digest,'renderer':'PDFium'},
            'coordinateSystem':'top-left source design units; PNGs rendered at one pixel per design unit',
            'layers':layers,**rectangles(),
            'runtimeStyle':{'actionText':'white-fill-without-blue-outline'},
            'excludedStaticData':['sample parameter values','sample photo metadata','gray photo placeholders','version and EID placeholder','live field labels','history and section titles'],
            'note':'Decorative artwork and live-control anchors are separate; static-composite is intentionally not a full-page reproduction.'}
    (args.output/'layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'layers':layers},ensure_ascii=False))


if __name__=='__main__':
    main()
