"""Extract the second approved artboard's foreground; never alter the PDF.

Runtime uses the current workspace snapshot, not the source's sample backdrop.
Paint-operation selections retain source transforms, clipping and color state.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from PIL import ImageOps

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject
import pypdfium2 as pdfium

ROOT=Path(__file__).resolve().parents[1]
SHA256='945627123d706f889935ad8ccfc88589b9c85ad7171ea7a5737bd5c2d3c6591e'
PAINT={b'f',b'F',b'f*',b'S',b's',b'B',b'B*',b'b',b'b*',b'Do',b'sh',b'Tj',b'TJ',b"'",b'"'}
PATH_PAINT={b'f',b'F',b'f*',b'S',b's',b'B',b'B*',b'b',b'b*'}
SELECTIONS={'surface':[(50,123),(561,590)],'title':[(124,361)],
            'next':[(385,482)],'exit':[(483,560)],
            'pattern':[(83,99)],'illustration':[(100,123)]}


def selected_page(reader,ranges,*,omit_strokes=False):
    writer=PdfWriter();page=writer.add_page(reader.pages[1])
    source=ContentStream(reader.pages[1]['/Contents'],reader)
    operations=[]
    for index,(args,operator) in enumerate(source.operations):
        if ((operator in PAINT and not any(first<=index<=last for first,last in ranges))
                or (omit_strokes and operator in (b'S',b's'))):
            if operator in PATH_PAINT:operations.append(([],b'n'))
            continue
        operations.append((args,operator))
    stream=ContentStream(None,writer);stream.operations=operations
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output);return output.getvalue()


def render(data):
    document=pdfium.PdfDocument(data);page=document[0]
    bitmap=page.render(scale=1,fill_color=(0,0,0,0))
    image=bitmap.to_pil().convert('RGBA').copy()
    bitmap.close();page.close();document.close();return image


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'排版.pdf')
    parser.add_argument('--output',type=Path,default=ROOT/'app/assets/completion')
    args=parser.parse_args()
    digest=hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest!=SHA256:raise RuntimeError('Source changed: verify page-2 selections before extraction.')
    args.output.mkdir(parents=True,exist_ok=True);reader=PdfReader(args.source);layers=[]
    for name,ranges in SELECTIONS.items():
        full=render(selected_page(reader,ranges,omit_strokes=name in ('exit','next')))
        if name=='illustration':
            alpha=full.getchannel('A');full=ImageOps.grayscale(full).convert('RGBA');full.putalpha(alpha)
        bounds=full.getchannel('A').getbbox()
        if bounds is None:raise RuntimeError('Empty selection: '+name)
        crop=full.crop(bounds);file=args.output/(name+'.png');crop.save(file,optimize=True)
        layers.append({'name':name,'file':file.name,'rect':[bounds[0],bounds[1],bounds[2]-bounds[0],bounds[3]-bounds[1]],
                       'paintRanges':ranges,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    layout={'schemaVersion':1,'source':{'file':'排版.pdf','page':2,'sha256':digest,'renderer':'PDFium'},
            'artboard':[4096,2160],'referenceScale':1/3,
            'panelRect':[1213.18,740.123,1874.398,788.648],'radius':50,
            'panelCenterRatio':[.525,.525],
            'buttons':{'exit':{'rect':[2227.5688,1344.7687,383.523,110.551],'radius':50},
                       'next':{'rect':[2631.7698,1344.7687,383.523,110.551],'radius':50}},
            'numberRect':[1390.501,1125.104,276,156],
            'pathTitleRect':[1699.8887,1125.104,1190,60],
            'pathRect':[1699.8887,1185.104,1190,117],
            'layers':layers,
            'excludedStaticData':['sample workspace image','sample path','sample number'],
            'runtimeStyle':{'actionText':'white-fill-without-blue-outline','illustration':'grayscale-preserving-alpha'},
            'runtime':{'background':'current-workspace-blurred-snapshot','longPath':'word-wrap-or-scroll',
                       'numberRange':[0,9],'exit':'return-to-welcome','next':'return-to-current-workspace'}}
    (args.output/'layout.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output),'layers':layers},ensure_ascii=False))


if __name__=='__main__':main()
