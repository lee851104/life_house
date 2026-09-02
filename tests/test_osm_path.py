"""libosmium 中文路徑繞道的測試。

這支繞道不是預防性的潔癖：專案資料夾就叫「LH專案」，沒有它的話
建立路網.py / 建立市界.py / 建立地名.py 在開發機上一律失敗，
而且錯誤訊息只有一句 "Open failed ... unknown error"，看不出跟路徑有關。
"""
import os

from src.data.osm_path import _is_ascii, readable_osm_path


def test_ascii_detection():
    assert _is_ascii(r"C:\lifehouse\raw\taiwan.osm.pbf")
    assert not _is_ascii(r"C:\Users\User\Desktop\LH專案\raw\taiwan.osm.pbf")


def test_ascii_path_is_returned_unchanged(tmp_path):
    src = tmp_path / "plain.osm.pbf"
    src.write_bytes(b"osm")
    assert readable_osm_path(str(src)) == os.path.abspath(str(src))


def test_non_ascii_path_is_bridged_to_an_ascii_one(tmp_path):
    folder = tmp_path / "專案"
    folder.mkdir()
    src = folder / "taiwan-latest.osm.pbf"
    src.write_bytes(b"osm-payload")

    bridged = readable_osm_path(str(src))

    assert _is_ascii(bridged), "繞道後的路徑必須是純 ASCII，否則等於沒換"
    assert os.path.exists(bridged)
    assert open(bridged, "rb").read() == b"osm-payload", "內容必須一致"
    assert os.path.exists(str(src)), "原始檔不可被移動或刪除"


def test_bridging_is_cached_per_source(tmp_path):
    folder = tmp_path / "路網"
    folder.mkdir()
    src = folder / "a.osm.pbf"
    src.write_bytes(b"x")
    assert readable_osm_path(str(src)) == readable_osm_path(str(src))


def test_same_basename_from_different_folders_do_not_collide(tmp_path):
    """兩份同名的 PBF 必須拿到不同的連結，否則會互刪對方正在讀的檔案。"""
    a = tmp_path / "甲" / "taiwan-latest.osm.pbf"
    b = tmp_path / "乙" / "taiwan-latest.osm.pbf"
    for path, payload in ((a, b"aaa"), (b, b"bbbb")):
        path.parent.mkdir()
        path.write_bytes(payload)

    la, lb = readable_osm_path(str(a)), readable_osm_path(str(b))

    assert la != lb
    assert open(la, "rb").read() == b"aaa"
    assert open(lb, "rb").read() == b"bbbb"
