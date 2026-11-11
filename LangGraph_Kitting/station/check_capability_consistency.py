"""Step 0 'done' check: every name in capability.yaml exists in the RoboDK
station, and every target/part in the station that the capability file
should know about is named there too (NFR2).

Run with RoboDK already open and Kitting_SSM3_Station built:
    python check_capability_consistency.py
"""

import sys
from pathlib import Path

import yaml
from robodk.robolink import Robolink

CAPABILITY_PATH = Path(__file__).resolve().parent.parent / "config" / "capability.yaml"


def main() -> int:
    with CAPABILITY_PATH.open("r", encoding="utf-8") as stream:
        capability = yaml.safe_load(stream)

    rdk = Robolink()
    station = rdk.Item("Kitting_SSM3_Station", 1)
    if not station.Valid():
        print("FAIL: Kitting_SSM3_Station is not open in RoboDK")
        return 1

    expected_names = set(capability["positions"]) | set(capability["parts"])
    station_names = {item.Name() for item in rdk.ItemList()}

    missing_from_station = sorted(expected_names - station_names)
    extra_model_names = {
        name for name in station_names
        if name.endswith("_Model")
    }
    unexpected_in_station = sorted(
        (station_names - expected_names - extra_model_names)
        & {n for n in station_names if n.startswith(("BIN_", "SLOT_", "RETURN", "HOME"))}
    )

    ok = True
    if missing_from_station:
        ok = False
        print("FAIL: capability names missing from the station:")
        for name in missing_from_station:
            print(f"  - {name}")
    if unexpected_in_station:
        ok = False
        print("FAIL: station names not declared in the capability file:")
        for name in unexpected_in_station:
            print(f"  - {name}")

    if ok:
        print(f"OK: {len(expected_names)} names match in both directions")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
