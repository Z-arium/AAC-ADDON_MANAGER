#!/bin/sh
# The desktop app, or the command line when called as `aac-linux-launcher cli ...`
# (flatpak run io.github.z_arium.ArcheAgeClassicLauncher cli doctor).
if [ "${1:-}" = cli ]; then
  shift
  exec aacam "$@"
fi
exec aacam-gui "$@"
