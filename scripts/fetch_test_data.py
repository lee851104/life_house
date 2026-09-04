"""Fetch the pinned release fixture for real-backend browser tests."""
import hashlib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {"事故索引.db", "路網.npz", "地名.db", "市界.npz", "基準.npz"}
SHA256 = "0485717325d8287bf82b8fe8bc1463dc8ccc3ca3034976869e6688eb581e4fc1"
URL = ("https://github.com/ruru0109lee-cpu/lifehouse/releases/download/v1.0.0/"
       "lifehouse-data-v1.0.0.zip")


def main():
    if all((ROOT / name).exists() for name in FILES):
        print("Using existing local data files.")
        return
    archive = ROOT / "raw" / "lifehouse-data-v1.0.0.zip"
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        urllib.request.urlretrieve(URL, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Release data checksum mismatch")
    with zipfile.ZipFile(archive) as zipped:
        if set(zipped.namelist()) != FILES or zipped.testzip() is not None:
            raise RuntimeError("Unexpected release archive contents")
        for name in FILES:
            if not (ROOT / name).exists():
                (ROOT / name).write_bytes(zipped.read(name))
    print("Release fixture verified and ready.")


if __name__ == "__main__":
    main()
