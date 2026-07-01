#!/bin/bash
cd /home/dev/rmi/backend

echo "=== Step 1: Auto-fix with ruff ==="
ruff check app/ --fix --unsafe-fixes

echo "=== Step 2: Count remaining errors ==="
ruff check app/ 2>&1 | grep "Found" | tail -1

echo "=== Step 3: Fix B904 (raise from None) ==="
# Find files with B904 and add 'from None' to raise statements
python3 -c "
import re
import os

result = os.popen('ruff check app/ 2>&1').read()

# Find files with B904
b904_files = set()
lines = result.split('\n')
current_file = None
for i, line in enumerate(lines):
    file_match = re.search(r'   -->\s*(app/[^:]+):', line)
    if file_match:
        current_file = file_match.group(1)
    if 'B904' in line and current_file:
        b904_files.add(current_file)

print(f'Files with B904: {len(b904_files)}')

count = 0
for filepath in b904_files:
    if not os.path.exists(filepath):
        continue
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            new_lines.append(line)
            
            # Check if this line ends 'except' (no 'as')
            if re.match(r'^\s*except\s*:$', line):
                # Look ahead for raise within next 5 lines
                j = i + 1
                while j < min(i + 6, len(lines)):
                    if 'raise ' in lines[j] and ' from ' not in lines[j]:
                        indent = len(lines[j]) - len(lines[j].lstrip())
                        new_lines.append(' ' * indent + 'from None\n')
                        break
                    new_lines.append(lines[j])
                    if 'raise' in lines[j]:
                        break
                    j += 1
                i = j + 1
            else:
                i += 1
        
        if new_lines != lines:
            with open(filepath, 'w') as f:
                f.writelines(new_lines)
            count += 1
            print(f'Fixed B904: {filepath}')
    except Exception as e:
        print(f'Error: {filepath} - {e}')

print(f'Fixed {count} files with B904')
"

echo "=== Step 4: Count again ==="
ruff check app/ 2>&1 | grep "Found" | tail -1
