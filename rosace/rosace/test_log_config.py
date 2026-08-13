"""
Tests of the centralized logging setup (log_config.py).
Run: pytest test_log_config.py -v
"""

import logging
import time

from rosace.log_config import configure_logging


class TestConfigureLogging:

    def test_formatter_uses_utc_not_local_time(self, tmp_data_dir):
        configure_logging()
        assert logging.Formatter.converter is time.gmtime
