"""PyInstaller entry point: the desktop app, or the CLI when called as `... cli <args>`."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from aacam.cli import main as cli_main

    sys.exit(cli_main(sys.argv[2:]))

from aacam.gui import main

sys.exit(main())
