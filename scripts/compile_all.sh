#!/usr/bin/env bash

set -euo pipefail

# Compile all ChampSim binaries for every configuration under dpc4/
# Usage: ./scripts/compile_all.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_DIR="dpc4"

if [[ ! -d "$CONFIG_DIR" ]]; then
  echo "Error: Config directory '$CONFIG_DIR' not found relative to repo root ($ROOT_DIR)." >&2
  exit 1
fi

shopt -s nullglob
configs=("$CONFIG_DIR"/*.json)
shopt -u nullglob

if [[ ${#configs[@]} -eq 0 ]]; then
  echo "No configuration files found in '$CONFIG_DIR'." >&2
  exit 1
fi

echo "Found ${#configs[@]} configuration(s) in '$CONFIG_DIR'."

for cfg in "${configs[@]}"; do
  echo "\n==> Configuring with: $cfg"
  ./config.sh "$cfg"

  echo "==> Building (make)"
  make

  echo "==> Completed build for: $cfg"
done

echo "\nAll builds completed successfully. Binaries should be in 'bin/'."
