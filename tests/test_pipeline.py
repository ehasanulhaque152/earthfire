import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "name": "test", "bbox": [-125, 32, -114, 42],
            "start_date": "2019-08-01", "end_date": "2019-08-14", "grid_km": 10,
            "sources": ["MODIS_SP", "VIIRS_SNPP_SP"],
        }

    def test_api_windows_are_at_most_five_days(self):
        self.assertEqual(list(pipeline.windows(date(2019, 8, 1), date(2019, 8, 14))),
                         [(date(2019, 8, 1), 5), (date(2019, 8, 6), 5), (date(2019, 8, 11), 4)])

    def test_time_padding_and_grid(self):
        row = {"latitude": "37.1", "longitude": "-120.1", "acq_date": "2019-08-01", "acq_time": "3",
               "satellite": "T", "confidence": "80", "daynight": "N"}
        item = pipeline.parse_row(row, "MODIS_SP", "test.csv", self.config)
        self.assertEqual(item["acquired_at_utc"], "2019-08-01T00:03:00Z")
        self.assertEqual(item["sensor"], "MODIS")
        self.assertEqual(item["cell_id"], pipeline.grid_cell(37.1, -120.1, self.config["bbox"], 10)[2])

    def test_provisional_calibration_and_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            raw = base / "raw"
            raw.mkdir()
            fields = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "daynight", "frp", "version"]
            for source in self.config["sources"]:
                path = raw / f"{source}_2019-08-01_5.csv"
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    for day in range(1, 15):
                        row = {"latitude": 37.1, "longitude": -120.1, "acq_date": f"2019-08-{day:02d}",
                               "acq_time": "1300", "satellite": "T" if source == "MODIS_SP" else "N",
                               "confidence": "80" if source == "MODIS_SP" else "n", "daynight": "D", "frp": "10", "version": "test"}
                        writer.writerow(row)
                        if source == "MODIS_SP":
                            writer.writerow(row)  # duplicate must not inflate activity
                        else:
                            writer.writerow({**row, "longitude": -119.9})
            report = pipeline.prepare(self.config, base)
            self.assertEqual(report["normalized_records"], 42)
            self.assertEqual(report["paired_sensor_daynight_bins"], 14)
            self.assertEqual(report["provisional_viirs_to_modis_factor"], 2)
            with (base / "provisional_calendar.csv").open(newline="", encoding="utf-8") as handle:
                calendar = list(csv.DictReader(handle))
            self.assertEqual(len(calendar), 14)
            self.assertEqual(calendar[0]["coverage"], "unknown_hotspots_only")

    def test_mixed_processing_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({**self.config, "sources": ["MODIS_SP", "VIIRS_SNPP_NRT"]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                pipeline.load_config(path)


if __name__ == "__main__":
    unittest.main()
