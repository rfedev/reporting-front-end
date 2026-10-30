#!/usr/bin/env python3
"""Executable launcher for the Reporting Front End desktop application."""

import logging
import sys
import warnings

# Suppress noisy Google Cloud SDK ADC warnings and missing default project logs
warnings.filterwarnings("ignore", category=UserWarning, module="google.auth._default")
logging.getLogger("google.auth._default").setLevel(logging.ERROR)

from reporting_app.main import main

if __name__ == "__main__":
    sys.exit(main())
