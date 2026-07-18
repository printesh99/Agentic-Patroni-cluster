#!/usr/bin/env python3
"""
Extract mermaid blocks from docs/COMPLETE_PROJECT_OVERVIEW.md, write .mmd files to tmp/,
produce a rendered markdown with image links at docs/_rendered_for_pdf.md

Usage: python3 tools/render_docs.py
"""
import re
from pathlib import Path

SRC = Path('docs/COMPLETE_PROJECT_OVERVIEW.md')
TMP = Path('tmp')
ASSETS = Path('docs/assets')
OUT = Path('docs/_rendered_for_pdf.md')

TMP.mkdir(parents=True, exist_ok=True)
ASSETS.mkdir(parents=True, exist_ok=True)

text = SRC.read_text(encoding='utf-8')

pattern = re.compile(r'```mermaid\n(.*?)\n```', re.DOTALL)

parts = []
idx = 0

def mmd_name(i):
    return f'diagram_{i:03d}.mmd'

# Find and extract mermaid blocks
for m in pattern.finditer(text):
    idx += 1
    fname = TMP / mmd_name(idx)
    content = m.group(1).strip() + '\n'
    fname.write_text(content, encoding='utf-8')

# Replace mermaid blocks with image links referencing assets/diagram_XXX.png
def replacement_generator():
    i = 0
    while True:
        i += 1
        yield f'![](assets/diagram_{i:03d}.png)'

rep_gen = replacement_generator()
new_text = pattern.sub(lambda _m: next(rep_gen), text)

OUT.write_text(new_text, encoding='utf-8')
print(f'Extracted {idx} mermaid blocks to {TMP} and wrote rendered markdown to {OUT}')
