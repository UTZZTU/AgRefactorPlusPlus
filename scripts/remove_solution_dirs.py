#!/usr/bin/env python3
"""
Simple script to remove all subdirectories containing the keyword "solution"
within a specified base directory.

Usage: python remove_solution_dirs.py <base_directory>
"""

import os
import sys
import shutil

def main():
    if len(sys.argv) != 2:
        print("Usage: python remove_solution_dirs.py <base_directory>")
        sys.exit(1)
    
    base_dir = sys.argv[1]
    
    if not os.path.exists(base_dir):
        print(f"Error: Directory '{base_dir}' does not exist.")
        sys.exit(1)
    
    if not os.path.isdir(base_dir):
        print(f"Error: '{base_dir}' is not a directory.")
        sys.exit(1)
    
    dirs_to_remove = []
    
    # Find all directories containing "solution" in their name
    for root, dirs, files in os.walk(base_dir):
        for dir_name in dirs:
            if "solution" in dir_name.lower():
                full_path = os.path.join(root, dir_name)
                dirs_to_remove.append(full_path)
    
    if not dirs_to_remove:
        print("No directories containing 'solution' found.")
        return
    
    # Show what will be removed
    print(f"Found {len(dirs_to_remove)} directories to remove:")
    for dir_path in dirs_to_remove:
        print(f"  - {dir_path}")
    
    # Confirm before removal
    response = input("\nProceed with removal? (y/N): ")
    if response.lower() != 'y':
        print("Aborted.")
        return
    
    # Remove directories
    for dir_path in dirs_to_remove:
        try:
            shutil.rmtree(dir_path)
            print(f"Removed: {dir_path}")
        except Exception as e:
            print(f"Error removing {dir_path}: {e}")
    
    print("Done.")

if __name__ == "__main__":
    main()
