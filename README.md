# MODIS–VIIRS hotspot pilot pipeline

This is a working first stage for the [2026 NASA Space Apps harmonization challenge](https://www.spaceappschallenge.org/2026/challenges/harmonization-of-modis-and-viirs-hot-spots/). It downloads FIRMS hotspot CSV files, keeps their source fields, standardizes UTC time, removes exact duplicates, assigns a regional approximately equal-area grid, and makes a provisional daily activity calendar. It uses Python 3.10+ with no third-party packages.

**Current status:** The code has been tested with a real [NASA FIRMS public VIIRS sample](https://firms.modaps.eosdis.nasa.gov/content/academy/data_ingest/firms_data_ingest.html) and synthetic paired records. `sample_data/` contains 73 genuine California VIIRS hotspots from 2023-07-12. It contains no real MODIS data, so it cannot provide an empirical MODIS–VIIRS calibration yet. The California August 2019 download requires your free FIRMS MAP_KEY.

## Get and run the historical pilot

1. Request a free [FIRMS MAP_KEY](https://firms.modaps.eosdis.nasa.gov/api/map_key). Do not commit or share it.
2. In PowerShell, from this folder, enter the key and run:

   ```powershell
   $env:FIRMS_MAP_KEY = Read-Host -MaskInput 'FIRMS MAP_KEY'
   python .\pipeline.py all --config .\config.json --output .\data
   Remove-Item Env:FIRMS_MAP_KEY
   ```

   The default `config.json` requests California, 2019-08-01 through 2019-08-31, using `MODIS_SP`, `VIIRS_SNPP_SP`, and `VIIRS_NOAA20_SP`. The script makes requests in at most five-day chunks, as specified by the [FIRMS area API](https://firms.modaps.eosdis.nasa.gov/api/area/). Existing raw CSV files are reused if the run is interrupted.

3. To use your own area, copy `config.json` and change `name`, `bbox` (`west,south,east,north`), dates, and sources. Use a regional box that does not cross the antimeridian. Check product dates with the [FIRMS data availability API](https://firms.modaps.eosdis.nasa.gov/api/data_availability/). Do not mix `_NRT` and `_SP` sources in one run.

4. If you already downloaded FIRMS CSV files through the [archive tool](https://firms.modaps.eosdis.nasa.gov/download/), place them in `data/raw/` and name them with a source prefix, such as `MODIS_SP_2019-08-01_5.csv`; then run `python .\pipeline.py prepare --config .\config.json --output .\data`.

## Output files

| File | Meaning |
|---|---|
| `raw/*.csv` | Downloaded FIRMS records, retained without preprocessing |
| `download_manifest.json` | Download source, date window, and file size |
| `normalized_hotspots.csv` | Original source metadata, parsed UTC acquisition time, and grid cell |
| `daily_sensor_activity.csv` | Detected active cells and hotspot counts by UTC day, sensor, and day/night |
| `provisional_calendar.csv` | Side-by-side MODIS and VIIRS activity, with a provisional VIIRS-referenced estimate when overlap is sufficient |
| `qa_report.json` | Record counts, paired bins, provisional scaling factor, and status |

The pilot uses a spherical equal-area projection centered on the selected region to make approximately 10 km grid cells. It is designed for moderate regional boxes below 70° latitude. A production global application should use a rigorously defined global equal-area grid.

## What “harmonized” means here

The first comparison uses **unique active 10 km cells** instead of raw fire pixels. For day/night bins with positive detections from both sensors, it computes the ratio of VIIRS active cells to MODIS active cells. If there are at least 14 paired bins, the median ratio gives a **provisional area-specific scale factor**. Days without enough paired data remain uncalibrated. This is an engineering baseline, not a validated scientific fire-activity product.

Hotspot CSVs list detections, but they do not prove that a cell with no row was observed under clear conditions. The output marks coverage as `unknown_hotspots_only`; blank values must not be interpreted as zero fire activity. NASA specifically cautions about cloud and missing-data effects in hotspot-only analysis. [FIRMS caveats](https://forum.earthdata.nasa.gov/viewtopic.php?t=5188)

The next scientific step is to add MODIS `MOD14/MYD14` and VIIRS `VNP14IMG` **fire-mask images** to distinguish valid non-fire observations from cloud or missing pixels. Then match comparable satellite passes by location and observation time, estimate sensor-specific detection probabilities, and validate on held-out years and regions. [MODIS fire product guide](https://www.earthdata.nasa.gov/s3fs-public/2023-09/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf), [VIIRS fire product guide](https://www.earthdata.nasa.gov/s3fs-public/2024-07/VIIRS_C2_AF-375m_User_Guide_1.0.pdf)

## Verify

```powershell
python -m unittest discover -s .\tests -v
python .\pipeline.py prepare --config .\sample_config.json --output .\sample_data
```

The real NASA sample should report **73 normalized records**, no paired MODIS–VIIRS bins, and a null calibration factor. Do not use the synthetic test records as research data.
