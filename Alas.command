#!/bin/bash
# macOS launcher, double click to start the Alas desktop shell.
# Same role as deploy/launcher/Alas.bat, but skips deploy.installer:
# requirements.txt is locked for Python 3.7 and cannot be installed on arm64.

cd "$(dirname "$0")" || exit 1

electron="webapp/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
# Electron starts the backend with this python
python=$(sed -n -E 's/^[[:space:]]*PythonExecutable:[[:space:]]*//p' config/deploy.yaml 2>/dev/null |
    tr -d '\r' | sed -E "s/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^[\"']//; s/[\"']$//")

for file in "$electron" "webapp/packages/main/dist/index.cjs" "config/deploy.yaml" "$python"; do
    if [ ! -e "$file" ]; then
        echo "Missing ${file:-PythonExecutable}, see \"macOS\" in CLAUDE.md section 2"
        read -r -p "Press enter to exit"
        exit 1
    fi
done

# NumpyOcr matrices are too small for multithreading, default OpenBLAS threads make OCR 20x slower
export OPENBLAS_NUM_THREADS=1

# Electron finds config/deploy.yaml from the working directory, so run it here instead of `open`
# Append to the log, a running instance is still writing it
mkdir -p log
nohup "$electron" webapp >> log/electron.txt 2>&1 &
disown
