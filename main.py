#!/usr/bin/env python3
"""
DEPRECATED: This entry point has been consolidated.

This file is maintained for backwards compatibility only.
All functionality is now in Webapp/main.py which includes:
  - Flask web application (dashboard, APIs)
  - KiteTicker real-time data subscription
  - 15-minute candle aggregation and database storage
  - Momentum/Reversal scanners (CK/VCP strategies)

To use the new unified entry point:
    python Webapp/main.py

This deprecation wrapper will be removed in a future release.
"""
from __future__ import annotations

import os
import sys
import logging
import importlib.util

# Setup logging
logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def main(argv=None):
	"""
	Deprecated entry point that delegates to Webapp/main.py.
	
	This function exists only for backwards compatibility.
	New code should use: python Webapp/main.py
	"""
	logger.warning(
		"\n"
		+ "=" * 70
		+ "\n⚠️  DEPRECATION WARNING: main.py is deprecated"
		+ "\n   Use: python Webapp/main.py instead"
		+ "\n   This compatibility wrapper will be removed in future versions."
		+ "\n" + "=" * 70
	)
	
	# Import and run from Webapp/main.py
	repo_root = os.path.dirname(os.path.abspath(__file__))
	webapp_main_path = os.path.join(repo_root, 'Webapp', 'main.py')
	
	if not os.path.exists(webapp_main_path):
		logger.error("ERROR: Webapp/main.py not found at %s", webapp_main_path)
		sys.exit(1)
	
	try:
		# Import Webapp/main.py as a module
		spec = importlib.util.spec_from_file_location("webapp_main", webapp_main_path)
		if not spec or not spec.loader:
			logger.error("ERROR: Failed to load spec for Webapp/main.py")
			sys.exit(1)
		
		webapp_module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(webapp_module)
		
		# Run with the provided arguments
		if hasattr(webapp_module, 'main'):
			webapp_module.main(argv)
		else:
			logger.error("ERROR: Webapp/main.py does not have a main() function")
			sys.exit(1)
	except Exception as e:
		logger.error("ERROR: Failed to run Webapp/main.py: %s", e)
		sys.exit(1)


if __name__ == '__main__':
	main()
