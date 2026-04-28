#!/usr/bin/env python3
"""
Rename lab from 'normattiva-lab' to 'italian-legal-lab'
Updates HuggingFace space/dataset names and all local references
"""

import os
from pathlib import Path
from huggingface_hub import HfApi, SpaceHardware

def rename_lab_on_huggingface():
    """Rename lab space and dataset on HuggingFace"""
    
    token = os.getenv('HF_TOKEN')
    if not token:
        raise ValueError("HF_TOKEN environment variable not set")
    
    api = HfApi(token=token)
    org = "diatribe00"
    old_space = f"{org}/normattiva-lab"
    old_dataset = f"{org}/normattiva-lab-data"
    new_space = f"{org}/italian-legal-lab"
    new_dataset = f"{org}/italian-legal-lab-data"
    
    print("="*70)
    print("LAB RENAME: normattiva-lab → italian-legal-lab")
    print("="*70)
    
    print(f"\n[1] Checking current space: {old_space}")
    try:
        space_info = api.space_info(old_space)
        print(f"    ✓ Found (status: {space_info.runtime.stage})")
    except Exception as e:
        print(f"    ✗ Not found or error: {e}")
        return False
    
    print(f"\n[2] Checking current dataset: {old_dataset}")
    try:
        dataset_info = api.dataset_info(old_dataset)
        print(f"    ✓ Found ({len(list(dataset_info.siblings))} files)")
    except Exception as e:
        print(f"    ✗ Not found or error: {e}")
        return False
    
    print(f"\n[3] NOTE: HuggingFace API does not support direct renaming")
    print(f"    RECOMMENDED MANUAL STEPS:")
    print(f"\n    A. On HuggingFace website:")
    print(f"       1. Go to https://huggingface.co/spaces/{old_space}")
    print(f"       2. Settings → Rename space → '{new_space.split('/')[-1]}'")
    print(f"       3. Go to https://huggingface.co/datasets/{old_dataset}")
    print(f"       4. Settings → Rename dataset → '{new_dataset.split('/')[-1]}'")
    print(f"\n    B. OR use git to preserve history:")
    print(f"       git clone https://huggingface.co/spaces/{old_space}")
    print(f"       cd {old_space.split('/')[-1]}")
    print(f"       git remote set-url origin https://huggingface.co/spaces/{new_space}")
    print(f"       git push")
    
    print(f"\n[4] After renaming, run:")
    print(f"       python clone_to_lab.py --use-new-names")
    
    return True

def update_local_references():
    """Update local file references to new lab name"""
    
    print("\n" + "="*70)
    print("UPDATING LOCAL FILE REFERENCES")
    print("="*70)
    
    files_to_update = [
        'clone_to_lab.py',
        'space/enhanced_lab_app.py',
    ]
    
    replacements = {
        'normattiva-lab': 'italian-legal-lab',
        'normattiva-lab-data': 'italian-legal-lab-data',
        'Normattiva Lab': 'Italian Legal Lab',
        'normattiva lab': 'italian legal lab',
        '"Normattiva"': '"Italian Legal System"',
    }
    
    for file_path in files_to_update:
        full_path = Path(file_path)
        if not full_path.exists():
            print(f"\n⚠ {file_path} not found")
            continue
        
        print(f"\n[*] Updating {file_path}")
        content = full_path.read_text(encoding='utf-8')
        original = content
        
        for old, new in replacements.items():
            if old in content:
                count = content.count(old)
                content = content.replace(old, new)
                print(f"    ✓ Replaced {count}x '{old}' → '{new}'")
        
        if content != original:
            full_path.write_text(content, encoding='utf-8')
            print(f"    ✓ File updated")
        else:
            print(f"    - No changes needed")
    
    return True

if __name__ == "__main__":
    print("\n🔄 ITALIAN LEGAL LAB REBRANDING\n")
    
    # Update HF
    rename_lab_on_huggingface()
    
    # Update local files
    update_local_references()
    
    print("\n" + "="*70)
    print("✓ REBRANDING PREPARATION COMPLETE")
    print("="*70)
    print("\nNext steps:")
    print("1. Manually rename space/dataset on HuggingFace (see instructions above)")
    print("2. Run: python clone_to_lab.py --skip-dataset")
    print("3. Verify at: https://huggingface.co/spaces/diatribe00/italian-legal-lab")
