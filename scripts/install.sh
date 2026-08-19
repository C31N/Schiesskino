#!/usr/bin/env bash
set -euo pipefail

# Kompatibler Einstieg für ältere Anleitungen. Die einzige maßgebliche
# Installation liegt im Projektstamm, damit beide Befehle nie unterschiedliche
# Systemstände erzeugen.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/../install.sh" "$@"
