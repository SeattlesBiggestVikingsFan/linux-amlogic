"""Validate and assemble a selection into an isolated index; never move refs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def select(catalog, requested=None):
    features = {}
    for item in catalog["features"]:
        if item["id"] in features:
            raise ValueError("Duplicate feature ID: " + item["id"])
        features[item["id"]] = item
    requested = requested if requested is not None else [
        f["id"] for f in features.values() if f["default"]]
    ordered, active, done = [], set(), set()
    def visit(fid):
        if fid not in features:
            raise ValueError("Unknown dependency/feature: " + fid)
        if fid in active:
            raise ValueError("Dependency cycle: " + fid)
        if fid in done:
            return
        item = features[fid]
        if item["status"] not in ("included", "upstream-integrated"):
            raise ValueError("Feature not admitted: " + fid)
        if not item["ordered_commits"]:
            review = item.get("upstream_review", {})
            if (item["status"] != "upstream-integrated" or
                    review.get("upstream_commit") != catalog["upstream"]["commit"] or
                    not review.get("evidence_sha256") or not review.get("reason")):
                raise ValueError("Missing pinned upstream-equivalence review: " + fid)
        active.add(fid)
        for dep in item["depends_on"]:
            visit(dep)
        active.remove(fid)
        done.add(fid)
        ordered.append(item)
    for fid in requested:
        visit(fid)
    return ordered


def assemble(repo, catalog, requested=None):
    chosen = select(catalog, requested)
    with tempfile.TemporaryDirectory(prefix="kernel-assembly-") as directory:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
        def git(*args, data=None):
            return subprocess.check_output(["git", "-C", str(repo), *args],
                                           input=data, env=env)
        git("read-tree", catalog["upstream"]["commit"])
        seen = set()
        for feature in chosen:
            if git("rev-parse", feature["ref"]).decode().strip() != feature["head"]:
                raise ValueError("Feature ref changed: " + feature["id"])
            parent = feature["base"]
            for commit in feature["ordered_commits"]:
                if commit in seen:
                    raise ValueError("Duplicate replay commit: " + commit)
                seen.add(commit)
                actual_parent = git("rev-parse", commit + "^").decode().strip()
                if actual_parent != parent:
                    raise ValueError("Not an ordered delta series: " + feature["id"])
                patch = git("diff", parent, commit, "--binary")
                git("apply", "--cached", "--whitespace=nowarn", "-", data=patch)
                parent = commit
            if feature["ordered_commits"] and parent != feature["head"]:
                raise ValueError("Feature head is not series endpoint: " + feature["id"])
        tree = git("write-tree").decode().strip()
    return {"tree": tree, "features": [f["id"] for f in chosen]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path(__file__).with_name("kernel-catalog.json"))
    parser.add_argument("--feature", action="append")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    result = assemble(args.repo, catalog, args.feature)
    if args.feature is None and result["tree"] != catalog["expected_tree"]:
        raise RuntimeError("Default assembly differs from locked accepted source tree")
    print(json.dumps(result, indent=2))
