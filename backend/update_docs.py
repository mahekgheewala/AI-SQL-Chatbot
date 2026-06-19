import os
import zipfile
from pathlib import Path

def update_docx(file_path, log_file):
    log_file.write(f"Processing {file_path}\n")
    temp_file = file_path + ".tmp"
    
    try:
        with zipfile.ZipFile(file_path, 'r') as zin:
            with zipfile.ZipFile(temp_file, 'w') as zout:
                for item in zin.infolist():
                    buffer = zin.read(item.filename)
                    if item.filename == 'word/document.xml':
                        xml_content = buffer.decode('utf-8')
                        
                        # Targeted replacements
                        xml_content = xml_content.replace('DB_USER=postgres', 'DB_USER=sqlassistant')
                        xml_content = xml_content.replace('DB_PASSWORD=yourpassword', 'DB_PASSWORD=CSUsql123')
                        xml_content = xml_content.replace('DB_SUPERDB=postgres', 'DB_SUPERDB=hr_database')
                        xml_content = xml_content.replace('DB_PASSWORD=...', 'DB_PASSWORD=CSUsql123')
                        xml_content = xml_content.replace('DB_USER: postgres', 'DB_USER: sqlassistant')
                        xml_content = xml_content.replace('DB_PASSWORD: yourpassword', 'DB_PASSWORD: CSUsql123')
                        
                        buffer = xml_content.encode('utf-8')
                    zout.writestr(item, buffer)
        
        # Replace the original file
        os.remove(file_path)
        os.rename(temp_file, file_path)
        log_file.write(f"Updated {file_path}\n")
    except Exception as e:
        log_file.write(f"Error updating {file_path}: {e}\n")
        if os.path.exists(temp_file):
            os.remove(temp_file)

def run_update():
    phases_dir = Path(__file__).resolve().parent.parent / "sql_phases"
    log_path = Path(__file__).resolve().parent / "update_log.txt"
    with open(log_path, 'w') as log_file:
        for i in range(1, 12):
            file_path = phases_dir / f"Phase {i}.docx"
            if file_path.exists():
                update_docx(str(file_path), log_file)
            else:
                log_file.write(f"File not found: {file_path}\n")

if __name__ == "__main__":
    run_update()
