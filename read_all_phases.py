import zipfile
import xml.etree.ElementTree as ET
import os

def extract_text_from_docx(filepath):
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    text_parts = []
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            with z.open('word/document.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
        
        for para in root.iter(ns + 'p'):
            para_texts = []
            for run in para.iter(ns + 't'):
                if run.text:
                    para_texts.append(run.text)
            line = ''.join(para_texts).strip()
            if line:
                text_parts.append(line)
        return '\n'.join(text_parts)
    except Exception as e:
        return f"ERROR EXTRACTING: {e}"

phases_dir = r"c:\Desktop\ai-sql-assistant\sql_phases"
output_path = r"c:\Desktop\ai-sql-assistant\phases_content.txt"

with open(output_path, 'w', encoding='utf-8') as out:
    for phase_num in range(1, 11):
        filepath = os.path.join(phases_dir, f"Phase {phase_num}.docx")
        if os.path.exists(filepath):
            out.write(f"\n{'='*80}\n")
            out.write(f"PHASE {phase_num}\n")
            out.write(f"{'='*80}\n\n")
            text = extract_text_from_docx(filepath)
            out.write(text)
            out.write('\n\n')

print(f"Done. Written all phases to {output_path}")
