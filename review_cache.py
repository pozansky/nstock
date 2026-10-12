"""Persistent, revision-aware cache for already computed strategy reviews."""

import hashlib
import json
import os
import uuid
from pathlib import Path


VERSION = "capital-review-v1"


def file_revision(paths):
    stamps = []
    for value in paths:
        path = Path(value)
        try:
            stat = path.stat()
            stamps.append([str(path), stat.st_mtime_ns, stat.st_size])
        except FileNotFoundError:
            stamps.append([str(path), None, None])
    return hashlib.sha256(json.dumps(stamps).encode()).hexdigest()


def cache_path(directory, identity):
    key = hashlib.sha256(str(identity or "default").encode()).hexdigest()[:24]
    return Path(directory) / (key + ".json")


def read(directory, identity, revision, history_directory):
    try:
        entry = json.loads(cache_path(directory, identity).read_text(encoding="utf-8"))
        paths = [Path(history_directory) / (code + ".json") for code in entry["codes"]]
        if entry.get("version") == VERSION and entry.get("revision") == revision and entry.get("history_revision") == file_revision(paths):
            return entry["review"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write(directory, identity, revision, history_directory, review):
    codes = sorted({item["code"] for row in review.get("rounds", []) + review.get("replay_rounds", [])
                    for item in row.get("detail", [])[:3]})
    payload = {"version": VERSION, "revision": revision, "codes": codes,
               "history_revision": file_revision([Path(history_directory) / (code + ".json") for code in codes]),
               "review": review}
    path = cache_path(directory, identity)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # Cache failure must not discard a successfully computed review.
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
