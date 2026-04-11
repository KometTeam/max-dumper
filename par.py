import re
import json

def parse_smali_enum(file_content):
    d2_pattern = re.compile(r'd2\s*=\s*\{(.*?)\}', re.DOTALL)
    match = d2_pattern.search(file_content)
    
    if not match:
        return "Could not find metadata array (d2)."

    raw_entries = match.group(1).split(',')
    clean_entries = [entry.strip().strip('"') for entry in raw_entries]
    
    try:
        start_index = clean_entries.index("fullContentString") + 3
        keys = clean_entries[start_index:]
    except ValueError:
        keys = clean_entries

    return {
        "class": "ru.ok.tamtam.android.prefs.PmsKey",
        "powered_by": "t.me/teamkomet",
        "total_keys": len(keys),
        "keys": keys
    }


smali_data = ""
with open("ex.txt", "r") as file:
    smali_data = file.read()
parsed_results = parse_smali_enum(smali_data)

print(json.dumps(parsed_results, indent=4))
