"""python -m fwapi 入口：等价于 `fw-api serve`。"""

import sys

from fwapi.serve import main

if __name__ == "__main__":
    sys.exit(main())
