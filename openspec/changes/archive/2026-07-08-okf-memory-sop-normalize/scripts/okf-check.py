#!/usr/bin/env python3
"""OKF v0.1 conformance check for memory/ bundle."""
import re, os, sys, yaml

bundle = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'memory')
ok = True
count = 0

for root, dirs, files in os.walk(bundle):
    if 'L4_raw_sessions' in root:
        continue
    for f in files:
        if not f.endswith('.md') or f in ('index.md', 'log.md'):
            continue
        path = os.path.join(root, f)
        content = open(path).read(2048)
        m = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not m:
            print(f'FAIL: {path} — no frontmatter')
            ok = False
            continue
        fm = yaml.safe_load(m.group(1))
        if not fm or not fm.get('type'):
            print(f'FAIL: {path} — missing type')
            ok = False
            continue
        count += 1

print(f'Checked {count} files')
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
