"""
Read Phase 1 and Phase 2 docx files using zipfile + xml parsing (no external deps needed).
Output to a text file for reading.
"""
import zipfile
import xml.etree.ElementTree as ET
import os
import sys

def extract_text_from_docx(filepath):
    """Extract plain text from a .docx file using only stdlib."""
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    text_parts = []
    with zipfile.ZipFile(filepath, 'r') as z:
        with z.open('word/document.xml') as f:
            tree = ET.parse(f)
            root = tree.getroot()
    
    # Walk all paragraphs
    for para in root.iter(ns + 'p'):
        para_texts = []
        for run in para.iter(ns + 't'):
            if run.text:
                para_texts.append(run.text)
        line = ''.join(para_texts).strip()
        if line:
            text_parts.append(line)
    return '\n'.join(text_parts)

phases_dir = r"C:\Desktop\ai-sql-assistant\sql_phases"
output_path = r"C:\Users\Hp\.gemini\antigravity-ide\brain\a0e510f1-d4bd-436c-9592-f40e9ca19d8d\scratch\phases_content.txt"

with open(output_path, 'w', encoding='utf-8') as out:
    for phase_num in [1, 2]:
        filepath = os.path.join(phases_dir, f"Phase {phase_num}.docx")
        out.write(f"\n{'='*80}\n")
        out.write(f"PHASE {phase_num}\n")
        out.write(f"{'='*80}\n\n")
        text = extract_text_from_docx(filepath)
        out.write(text)
        out.write('\n\n')

print(f"Done. Written to {output_path}")
