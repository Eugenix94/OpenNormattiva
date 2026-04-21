#!/usr/bin/env python3
"""
Pre-deployment check - verify all required files are present before deploying.
"""
import sys
from pathlib import Path

def check_file(path, name, min_size_mb=0):
    """Check if a file exists and optionally verify minimum size."""
    p = Path(path)
    if not p.exists():
        print(f"  ❌ MISSING: {name} ({path})")
        return False
    
    size_mb = p.stat().st_size / 1e6
    if size_mb < min_size_mb:
        print(f"  ❌ TOO SMALL: {name} - {size_mb:.1f} MB (expected > {min_size_mb} MB)")
        return False
    
    print(f"  ✓ {name} - {size_mb:.1f} MB")
    return True

def main():
    print("\nPre-deployment checklist:")
    print("=" * 50)
    
    all_good = True
    
    # Check required files
    print("\n1. Source code files:")
    all_good &= check_file("space/app.py", "Streamlit app")
    all_good &= check_file("deploy_hf.py", "Deploy script")
    all_good &= check_file("normattiva_api_client.py", "API client")
    
    print("\n2. Database:")
    all_good &= check_file("data/laws.db", "Pre-built database", min_size_mb=700)
    
    print("\n3. Core package:")
    all_good &= check_file("core/db.py", "Database layer")
    all_good &= check_file("core/changelog.py", "Changelog tracker")
    all_good &= check_file("core/legislature.py", "Legislature metadata")
    
    print("\n4. Dependencies:")
    all_good &= check_file("requirements.txt", "Python requirements")
    
    print("\n5. GitHub Actions:")
    all_good &= check_file(".github/workflows/check-changes.yml", "Change detection workflow")
    
    # Verify app.py syntax
    print("\n6. Code validation:")
    try:
        import ast
        with open("space/app.py", "r", encoding="utf-8") as f:
            ast.parse(f.read())
        print("  ✓ app.py syntax valid")
    except SyntaxError as e:
        print(f"  ❌ app.py has syntax error: {e}")
        all_good = False
    
    # Summary
    print("\n" + "=" * 50)
    if all_good:
        print("✅ All checks passed! Ready to deploy.")
        print("\nNext steps:")
        print("  1. Set HF_TOKEN: $env:HF_TOKEN = 'hf_xxx'")
        print("  2. Deploy: python deploy_now.py")
        return 0
    else:
        print("❌ Some checks failed. Fix issues before deploying.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
