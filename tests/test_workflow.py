"""Verify maintained feature selection, explicit dependencies and source parity."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--catalog", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("assembly", Path(__file__).parents[1] / "assemble.py")
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)
catalog = json.loads(args.catalog.read_text())
all_result = assembly.assemble(args.repo, catalog)
assert all_result["tree"] == catalog["expected_tree"]
rows = []
for f in catalog["features"]:
    if f["default"]:
        one = assembly.assemble(args.repo, catalog, [f["id"]])
        expected = subprocess.check_output(
            ["git", "-C", str(args.repo), "rev-parse", f["head"] + "^{tree}"]).decode().strip()
        assert one["tree"] == expected, f["id"]
        rows.append({"feature": f["id"], "standalone_with_dependencies": True, "tree": one["tree"]})
without_ethernet = [f["id"] for f in catalog["features"] if f["default"] and f["id"] != "ethernet-calibration-8a"]
no_eth = assembly.assemble(args.repo, catalog, without_ethernet)
assert no_eth["tree"] != catalog["expected_tree"]
eth = next(f for f in catalog["features"] if f["id"] == "ethernet-calibration-8a")
changed = subprocess.check_output(
    ["git", "-C", str(args.repo), "diff", "--name-only", no_eth["tree"], all_result["tree"]]).decode().splitlines()
assert sorted(changed) == sorted(eth["paths"])
negative_results = []
for kind in ("missing", "cycle", "duplicate", "unreviewed-skip"):
    bad = copy.deepcopy(catalog)
    feature = bad["features"][0]
    if kind == "missing":
        feature["depends_on"] = ["missing"]
    elif kind == "cycle":
        feature["depends_on"] = [feature["id"]]
    elif kind == "duplicate":
        bad["features"].append(copy.deepcopy(feature))
    else:
        feature["ordered_commits"] = []
        feature["status"] = "upstream-integrated"
        feature.pop("upstream_review", None)
    try:
        assembly.select(bad)
    except ValueError:
        negative_results.append(kind)
    else:
        raise AssertionError(kind + " unexpectedly accepted")
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "workflow-tests.json").write_text(json.dumps({
    "passed": True, "all_default_tree": all_result["tree"], "standalone_cases": rows,
    "excluded_ethernet_changed_paths": changed, "rejected_invalid_catalogs": negative_results,
    "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
    "scope": "Offline assembly and source-tree validation, no build or device evidence."
}, indent=2) + "\n")
print("PASS default parity, %d dependency-closed feature assemblies, Ethernet omission and 4 invalid-catalog controls" % len(rows))
