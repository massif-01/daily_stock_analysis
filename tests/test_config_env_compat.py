# -*- coding: utf-8 -*-
"""Environment compatibility tests for TickFlow config loading."""

import os
import unittest
from unittest.mock import patch

from src.config import Config


class ConfigEnvCompatibilityTestCase(unittest.TestCase):
    def tearDown(self):
        Config.reset_instance()

    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_load_from_env_reads_tickflow_api_key(self, _mock_parse_litellm_yaml):
        with patch.dict(
            os.environ,
            {
                "STOCK_LIST": "600519",
                "TICKFLOW_API_KEY": "tf-secret",
            },
            clear=True,
        ):
            config = Config._load_from_env()

        self.assertEqual(config.tickflow_api_key, "tf-secret")

    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_load_from_env_keeps_default_behavior_without_tickflow_api_key(
        self, _mock_parse_litellm_yaml
    ):
        with patch.dict(
            os.environ,
            {
                "STOCK_LIST": "600519",
            },
            clear=True,
        ):
            config = Config._load_from_env()

        self.assertIsNone(config.tickflow_api_key)
        self.assertEqual(
            config.realtime_source_priority,
            "tencent,akshare_sina,efinance,akshare_em",
        )


if __name__ == "__main__":
    unittest.main()
