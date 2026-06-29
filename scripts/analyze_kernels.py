#!/usr/bin/env python3
"""
Script to analyze kernel data from train_kernels.json and test_kernels_full.json.
Organizes kernels by category (first word before '_' in kernel name) and sorts by lines of code.
"""

import json
import os
from collections import defaultdict
from pathlib import Path

def count_lines_of_code(file_path):
    """Count non-empty lines of code in a file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        # Count non-empty lines (strip whitespace and check if non-empty)
        non_empty_lines = [line for line in lines if line.strip()]
        return len(non_empty_lines)
    except FileNotFoundError:
        print(f"Warning: File not found: {file_path}")
        return 0
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return 0

def get_category_from_kernel_name(kernel_name):
    """Extract category from kernel name (first word before '_')."""
    return kernel_name.split('_')[0] if '_' in kernel_name else kernel_name

def analyze_kernels():
    """Main function to analyze kernel data."""
    # Define file paths
    base_dir = Path(__file__).parent.parent
    train_file = base_dir / "flow" / "train_kernels.json"
    test_file = base_dir / "flow" / "test_kernels_full.json"
    src_dir = base_dir / "src"
    
    print(f"Base directory: {base_dir}")
    print(f"Source directory: {src_dir}")
    print()
    
    # Read JSON files
    all_kernels = []
    
    print("Reading kernel data files...")
    for json_file, dataset_name in [(train_file, "train"), (test_file, "test")]:
        if json_file.exists():
            try:
                with open(json_file, 'r') as f:
                    kernels = json.load(f)
                    print(f"Loaded {len(kernels)} kernels from {dataset_name} set")
                    for kernel in kernels:
                        kernel.append(dataset_name)  # Add dataset info
                    all_kernels.extend(kernels)
            except Exception as e:
                print(f"Error reading {json_file}: {e}")
        else:
            print(f"Warning: {json_file} not found")
    
    print(f"Total kernels to analyze: {len(all_kernels)}")
    print()
    
    # Organize kernels by category
    categories = defaultdict(list)
    
    print("Analyzing kernels and counting lines of code...")
    for i, kernel_data in enumerate(all_kernels):
        if len(kernel_data) < 4:  # Should have path, function, name, and dataset
            print(f"Warning: Invalid kernel data format: {kernel_data}")
            continue
            
        source_path, function_name, kernel_name, dataset = kernel_data
        
        # Get full source file path
        full_source_path = src_dir / source_path
        
        # Count lines of code
        loc = count_lines_of_code(full_source_path)
        
        # Get category
        category = get_category_from_kernel_name(kernel_name)
        
        # Store kernel info
        kernel_info = {
            'kernel_name': kernel_name,
            'function_name': function_name,
            'source_path': source_path,
            'full_path': str(full_source_path),
            'lines_of_code': loc,
            'dataset': dataset
        }
        
        categories[category].append(kernel_info)
        
        if (i + 1) % 10 == 0:
            print(f"Processed {i + 1}/{len(all_kernels)} kernels...")
    
    print(f"Completed analysis of {len(all_kernels)} kernels")
    print()
    
    # Sort kernels within each category by lines of code
    for category in categories:
        categories[category].sort(key=lambda x: x['lines_of_code'], reverse=True)
    
    # Display results
    print("=" * 80)
    print("KERNEL ANALYSIS RESULTS")
    print("=" * 80)
    print()
    
    # Summary statistics
    total_kernels = sum(len(kernels) for kernels in categories.values())
    total_loc = sum(kernel['lines_of_code'] for kernels in categories.values() for kernel in kernels)
    
    print(f"Total Categories: {len(categories)}")
    print(f"Total Kernels: {total_kernels}")
    print(f"Total Lines of Code: {total_loc}")
    print()
    
    # Display categories sorted by name
    for category in sorted(categories.keys()):
        kernels = categories[category]
        category_loc = sum(k['lines_of_code'] for k in kernels)
        
        print(f"Category: {category.upper()}")
        print(f"  Kernels: {len(kernels)}")
        print(f"  Total LOC: {category_loc}")
        print(f"  Avg LOC: {category_loc / len(kernels):.1f}")
        print()
        
        # Display kernels sorted by LOC (descending)
        for kernel in kernels:
            dataset_label = f"[{kernel['dataset'].upper()}]"
            print(f"    {kernel['lines_of_code']:4d} LOC  {dataset_label:7s} {kernel['kernel_name']:35s} ({kernel['function_name']})")
            print(f"         {kernel['source_path']}")
        print()
    
    # Summary by dataset
    print("=" * 80)
    print("SUMMARY BY DATASET")
    print("=" * 80)
    print()
    
    dataset_stats = defaultdict(lambda: {'count': 0, 'loc': 0})
    for kernels in categories.values():
        for kernel in kernels:
            dataset = kernel['dataset']
            dataset_stats[dataset]['count'] += 1
            dataset_stats[dataset]['loc'] += kernel['lines_of_code']
    
    for dataset in sorted(dataset_stats.keys()):
        stats = dataset_stats[dataset]
        avg_loc = stats['loc'] / stats['count'] if stats['count'] > 0 else 0
        print(f"{dataset.upper():10s}: {stats['count']:3d} kernels, {stats['loc']:6d} total LOC, {avg_loc:5.1f} avg LOC")
    
    print()
    print("Analysis complete!")

if __name__ == "__main__":
    analyze_kernels()


