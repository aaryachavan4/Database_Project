import unittest

from performance.analysis import (
    calculate_overhead,
    percentile,
    validate_comparability,
)


class AnalysisTests(unittest.TestCase):
    def test_positive_overhead(self):
        self.assertAlmostEqual(calculate_overhead(2.0, 2.4), 20.0)

    def test_negative_overhead(self):
        self.assertAlmostEqual(calculate_overhead(4.0, 3.0), -25.0)

    def test_zero_baseline_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_overhead(0.0, 2.0)

    def test_nearest_rank_p95(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10)

    def test_different_dataset_ids_are_rejected(self):
        baseline = {"q1": {
            "customers_rows": "10", "accounts_rows": "20", "transactions_rows": "30",
            "customers_id_sha256": "a", "accounts_id_sha256": "b", "transactions_id_sha256": "c",
            "server_version": "10.11", "buffer_pool_size_bytes": "1024",
            "failed_runs": "0", "successful_runs": "10",
        }}
        encrypted = {"q1": {
            "customers_rows": "10", "accounts_rows": "20", "transactions_rows": "30",
            "customers_id_sha256": "different", "accounts_id_sha256": "b", "transactions_id_sha256": "c",
            "server_version": "10.11", "buffer_pool_size_bytes": "1024",
            "failed_runs": "0", "successful_runs": "10",
        }}
        with self.assertRaises(ValueError):
            validate_comparability(baseline, encrypted)


if __name__ == "__main__":
    unittest.main()
