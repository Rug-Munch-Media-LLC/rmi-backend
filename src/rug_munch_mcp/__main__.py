"""Entry point for python -m rug_munch_mcp"""

import asyncio

from .server import main

asyncio.run(main())
