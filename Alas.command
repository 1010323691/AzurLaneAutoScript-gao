#!/bin/bash
# macOS launcher, double click to start the Alas desktop shell.
# Same role as deploy/launcher/Alas.bat, but skips deploy.installer:
# requirements.txt is locked for Python 3.7 and cannot be installed on arm64.

cd "$(dirname "$0")" || exit 1

electron="webapp/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"

for file in "$electron" "webapp/packages/main/dist/index.cjs" ".venv/bin/python" "config/deploy.yaml"; do
    if [ ! -e "$file" ]; then
        echo "Missing $file, see \"macOS\" in CLAUDE.md section 2"
        read -r -p "Press enter to exit"
        exit 1
    fi
done

# Electron finds config/deploy.yaml from the working directory, so run it here instead of `open`
mkdir -p log
nohup "$electron" webapp > log/electron.txt 2>&1 &
disown
