#!/bin/sh
# Contrôles du panneau : aucune écriture dans Premiere ni dans Resolve.
set -e
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p 'test_*.py' -q
node --test tests/panel_test.cjs tests/host_test.cjs
node --check panel.js
node --check < host.jsx
python3 -c "import xml.dom.minidom,sys;xml.dom.minidom.parse('CSXS/manifest.xml')"
echo 'Contrôles OK'
