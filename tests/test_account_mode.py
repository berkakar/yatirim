import unittest

from alpaca_client import DEFAULT_TRADING_URL, AlpacaClient, is_paper_url


class AccountModeTest(unittest.TestCase):
    def test_paper_and_live_urls(self):
        self.assertTrue(is_paper_url("https://paper-api.alpaca.markets/v2"))
        self.assertFalse(is_paper_url("https://api.alpaca.markets/v2"))

    def test_client_defaults_to_paper(self):
        self.assertTrue(AlpacaClient("k", "s").is_paper)
        self.assertTrue(is_paper_url(DEFAULT_TRADING_URL))
        self.assertFalse(AlpacaClient("k", "s", trading_url="https://api.alpaca.markets/v2").is_paper)


if __name__ == "__main__":
    unittest.main()
