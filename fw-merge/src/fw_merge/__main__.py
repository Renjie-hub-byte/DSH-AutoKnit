"""Allow ``python -m fw_merge ...`` as an alternative to the fw-merge script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
