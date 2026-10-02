"""Tests for the Print panel's HTTP API, with the emphasis on what it must refuse.

A loopback endpoint that writes files and runs a subprocess is only acceptable if it is
hard to misuse. These tests stand up the real server on an ephemeral port and check the
token, the Origin rule, id-only resolution, input bounds, and one full proof render.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panolib import printapi
from panolib.printapi import BadRequest, validate

_SERVER = {}


def _fixture_dir() -> str:
    """A tiny library with one real (synthetic) equirect and a placeholder index."""
    d = tempfile.mkdtemp(prefix="pano_api_")
    os.makedirs(os.path.join(d, "panoramas", "p1"), exist_ok=True)
    w, h = 400, 200
    lon = (np.arange(w) + 0.5) / w * 2 * np.pi - np.pi
    lat = np.pi / 2 - (np.arange(h) + 0.5) / h * np.pi
    LON, LAT = np.meshgrid(lon, lat)
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0] = (np.sin(LON) > 0) * 200 + 30
    img[..., 1] = (LAT > 0) * 200 + 30
    img[..., 2] = 90
    Image.fromarray(img).save(os.path.join(d, "panoramas", "p1", "eq.jpg"), quality=90)
    cov = {"lon_min": -180, "lon_max": 180, "lat_min": -90, "lat_max": 90}
    lib = {"panoramas": [{"id": "test-pano-1", "name": "100_0001", "status": "ok",
                          "equirect": "panoramas/p1/eq.jpg", "trip": "Test", "coverage": cov},
                         # same folder NAME, different panorama -- as 20 library entries are
                         {"id": "test-pano-1b", "name": "100_0001", "status": "ok",
                          "equirect": "panoramas/p1/eq.jpg", "trip": "Test", "coverage": cov}]}
    with open(os.path.join(d, "library.json"), "w", encoding="utf-8") as fh:
        json.dump(lib, fh)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
        fh.write('<html><meta name="print-token" content="__PRINT_TOKEN__"><body>x</body></html>')
    return d


def _server():
    if _SERVER:
        return _SERVER
    out = _fixture_dir()
    httpd = printapi.serve(out, 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    _SERVER.update(httpd=httpd, port=port, out=out, ctx=httpd.print_context,
                   base=f"http://127.0.0.1:{port}")
    return _SERVER


def _req(method, path, body=None, headers=None):
    s = _server()
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(s["base"] + path, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, None


def _good_body(**kw):
    b = {"id": "test-pano-1", "source": "view", "size": "5x7", "dpi": 100,
         "style": "gallery", "proof": True,
         "view": {"mode": "immersive", "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
                  "fov": 75, "morph": 0, "aspect": 1.5}}
    b.update(kw)
    return b


# ------------------------------------------------------------------ token & origin

def test_index_has_token_injected():
    s = _server()
    with urllib.request.urlopen(s["base"] + "/", timeout=10) as r:
        html = r.read().decode()
    assert "__PRINT_TOKEN__" not in html, "placeholder must be replaced when served"
    assert s["ctx"].token in html


def test_post_without_token_is_refused():
    code, body = _req("POST", "/api/print", _good_body())
    assert code == 403, (code, body)


def test_post_with_wrong_token_is_refused():
    code, _ = _req("POST", "/api/print", _good_body(), {"X-Print-Token": "nope"})
    assert code == 403


def test_cross_origin_post_is_refused_even_with_token():
    """Another website in the same browser can POST to localhost; the Origin header is
    the browser's own statement of where the request came from, and it is not ours."""
    s = _server()
    code, _ = _req("POST", "/api/print", _good_body(),
                   {"X-Print-Token": s["ctx"].token, "Origin": "https://evil.example"})
    assert code == 403


def test_same_origin_post_is_accepted():
    s = _server()
    code, body = _req("POST", "/api/print", _good_body(),
                      {"X-Print-Token": s["ctx"].token, "Origin": s["base"]})
    assert code == 202 and "job" in body, (code, body)


# ------------------------------------------------------------------ input bounds

def test_unknown_id_is_404_not_a_path_lookup():
    s = _server()
    code, _ = _req("POST", "/api/print", _good_body(id="does-not-exist"),
                   {"X-Print-Token": s["ctx"].token})
    assert code == 404


def test_path_shaped_id_is_rejected():
    for bad in ("../../etc/passwd", "C:\\Windows\\x", "a/b", "x" * 200, ""):
        try:
            validate(_good_body(id=bad))
        except BadRequest:
            continue
        raise AssertionError(f"id {bad!r} should be rejected")


def test_dpi_and_fov_are_clamped_and_title_is_cleaned():
    req = validate(_good_body(dpi=99999, title="Hello\x00\x1fWorld" + "!" * 300,
                              view={"mode": "immersive", "fov": 9999, "morph": 7, "yaw": 1}))
    assert req["dpi"] == printapi.DPI_MAX
    assert req["view"].fov == 330.0 and req["view"].morph == 1.0
    assert "\x00" not in req["title"] and len(req["title"]) <= printapi.TITLE_MAX
    assert req["title"].startswith("HelloWorld")


def test_bad_style_mode_and_size_are_400():
    """Bad input is refused at the door, not accepted as a job that fails later."""
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    assert _req("POST", "/api/print", _good_body(style="neon"), tok)[0] == 400
    assert _req("POST", "/api/print", _good_body(view={"mode": "cube"}), tok)[0] == 400
    code, body = _req("POST", "/api/print", _good_body(size="enormous"), tok)
    assert code == 400 and "paper size" in body["error"], (code, body)
    assert _req("POST", "/api/print", _good_body(size="0.5x0.5"), tok)[0] == 400
    assert _req("POST", "/api/print", _good_body(size="1x40"), tok)[0] == 400       # 40:1


def test_oversize_render_is_refused_up_front():
    """A1 at 400 dpi is 9354 x 13229 px: over the per-side limit -> 400, not a job."""
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    code, body = _req("POST", "/api/print", _good_body(size="a1", dpi=400, proof=False), tok)
    assert code == 400 and "limit" in body["error"], (code, body)
    # 40x40 inches at 300 dpi is 144 MP: under the side limit, over the pixel limit
    code, body = _req("POST", "/api/print", _good_body(size="40x40", dpi=300, proof=False), tok)
    assert code == 400 and "megapixels" in body["error"], (code, body)
    # and the stock panel's largest honest choice, 24x36 at 300 dpi (78 MP), is allowed
    assert printapi.check_paper(printapi.parse_size("24x36", 300, "landscape")) is None


def test_wrong_host_header_is_refused():
    """DNS rebinding: a page on attacker.example resolving to 127.0.0.1 presents its own
    Host header. Every method must refuse it, GET included, or the library leaks."""
    s = _server()
    for method, path in (("GET", "/library.json"), ("GET", "/"), ("HEAD", "/library.json"),
                         ("POST", "/api/print")):
        r = urllib.request.Request(s["base"] + path, method=method,
                                   data=b"{}" if method == "POST" else None,
                                   headers={"Host": f"attacker.example:{s['port']}",
                                            "X-Print-Token": s["ctx"].token,
                                            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 421, (method, path, code)


def test_directory_listing_is_off():
    s = _server()
    try:
        with urllib.request.urlopen(s["base"] + "/panoramas/", timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 403


def _raw_post(path, data: bytes, headers=None):
    s = _server()
    r = urllib.request.Request(s["base"] + path, data=data, method="POST",
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_malformed_bodies_are_400_not_500():
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    for body in ([1, 2, 3], "string", {"id": 5}, {"id": "test-pano-1", "style": 7},
                 {"id": "test-pano-1", "view": "x"}, {"id": "test-pano-1", "dpi": "1e999"}):
        code, _ = _req("POST", "/api/print", body, tok)
        assert code == 400, (body, code)
    # bytes that are not JSON at all, and not even UTF-8: the handler's own 400, not a 500
    for raw in (b"{", b"{not json", b"\xff\xfe\x00", b"[" * 5000):
        assert _raw_post("/api/print", raw, tok) == 400, raw[:10]


def test_oversize_body_is_413():
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    big = json.dumps({"id": "test-pano-1", "title": "x" * (printapi.MAX_BODY + 1024)}).encode()
    assert _raw_post("/api/print", big, tok) == 413


def test_title_is_cleaned_of_format_and_control_characters():
    req = validate(_good_body(title="Loch​ Lomond‮ \x07<b>x</b>"))
    assert "​" not in req["title"] and "‮" not in req["title"] and "\x07" not in req["title"]
    assert req["title"].startswith("Loch Lomond")
    assert "<b>" in req["title"]          # markup is kept as TEXT; the page renders it as text


def test_yaw_is_wrapped_not_clamped():
    req = validate(_good_body(view={"mode": "immersive", "yaw": 100.0}))   # ~16 turns
    import math as _m
    assert -_m.pi <= req["view"].yaw <= _m.pi
    assert abs(req["view"].yaw - _m.remainder(100.0, 2 * _m.pi)) < 1e-9


def _finish(body):
    for _ in range(600):
        _, j = _req("GET", f"/api/jobs/{body['job']}")
        if j["state"] in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_final_equirect_print_writes_sidecar_without_a_view_block():
    """Also pins the atomic write: the image and the sidecar must be WRITTEN under a
    .part name and only then renamed, which a spy on the two writers can see."""
    from panolib import _print_cmd
    s = _server()
    written = []
    real_save, real_side = printapi.save_print, _print_cmd.write_sidecar

    def spy_save(image, path, paper, **kw):
        written.append(path)
        return real_save(image, path, paper, **kw)

    def spy_side(path, cap, extra):
        written.append(path)
        return real_side(path, cap, extra)

    printapi.save_print, _print_cmd.write_sidecar = spy_save, spy_side
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
    finally:
        printapi.save_print, _print_cmd.write_sidecar = real_save, real_side
    assert j["state"] == "done", j
    res = j["result"]
    assert res["sidecar"] and os.path.isfile(res["path"])
    side_path = os.path.join(s["out"], res["sidecar"].lstrip("/"))
    assert [os.path.basename(w) for w in written] == [os.path.basename(res["path"]) + ".part",
                                                       os.path.basename(side_path) + ".part"], written
    assert not os.path.exists(res["path"] + ".part") and not os.path.exists(side_path + ".part")
    side = json.load(open(side_path, encoding="utf-8"))
    assert side["print"]["made_from"] == "viewer"
    assert "view" not in side["print"]
    assert "-landscape-" in os.path.basename(res["path"])      # 5x7 on a 2:1 source


def test_torn_write_leaves_no_output():
    """If the writer dies mid-file, neither the print nor a .part may remain."""
    s = _server()
    real_save = printapi.save_print

    def dying_save(image, path, paper, **kw):
        real_save(image, path, paper, **kw)
        raise OSError("disk full")

    printapi.save_print = dying_save
    try:
        code, body = _req("POST", "/api/print", _good_body(title="torn"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
    finally:
        printapi.save_print = real_save
    assert j["state"] == "error" and "disk full" in j["error"], j
    proofs = os.path.join(s["out"], "prints", "from-viewer", ".proofs")
    leftovers = [f for f in os.listdir(proofs) if f.endswith(".part")]
    assert not leftovers, leftovers


def test_final_view_print_records_the_framing_and_exposure():
    import math as _m
    s = _server()
    view = {"mode": "immersive", "yaw": 0.7, "pitch": 0.1, "roll": 0.0, "fov": 60,
            "morph": 0.25, "aspect": 1.5, "exposure": 1.3}
    code, body = _req("POST", "/api/print",
                      _good_body(proof=False, size="5x7", dpi=100, view=view),
                      {"X-Print-Token": s["ctx"].token})
    assert code == 202
    j = _finish(body)
    assert j["state"] == "done", j
    res = j["result"]
    side = json.load(open(os.path.join(s["out"], res["sidecar"].lstrip("/")), encoding="utf-8"))
    v = side["print"]["view"]
    assert abs(v["yaw_deg"] - _m.degrees(0.7)) < 0.01 and abs(v["pitch_deg"] - _m.degrees(0.1)) < 0.01
    assert v["fov_deg"] == 60.0 and v["morph"] == 0.25 and v["exposure"] == 1.3
    assert "view040" in os.path.basename(res["path"])          # 0.7 rad = 40.1 degrees


def test_landscape_and_portrait_of_the_same_view_are_different_files():
    """A view print's orientation follows the browser window. Two clicks of Print with
    the window resized in between must not overwrite each other."""
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    paths = []
    for aspect in (1.5, 0.6):
        view = {"mode": "immersive", "yaw": 0.5, "pitch": 0.0, "fov": 75, "aspect": aspect}
        code, body = _req("POST", "/api/print", _good_body(view=view), tok)
        assert code == 202
        j = _finish(body)
        assert j["state"] == "done", j
        paths.append(j["result"]["path"])
        assert (j["result"]["width"] > j["result"]["height"]) == (aspect > 1)
    assert paths[0] != paths[1]
    assert "-landscape-" in paths[0] and "-portrait-" in paths[1]
    assert all(os.path.isfile(p) for p in paths)


def test_sweep_touches_only_its_own_temp_files_and_never_follows_links():
    """The startup sweep must not walk (a junction inside the prints folder would lead
    it into another program's *.part downloads), must leave fresh files alone (another
    viewer on a different port may be mid-write), and must take only its own names."""
    out = tempfile.mkdtemp(prefix="pano_sweep_")
    fv = os.path.join(out, "prints", "from-viewer")
    os.makedirs(os.path.join(fv, ".proofs"))
    os.makedirs(os.path.join(fv, "sub"))
    elsewhere = tempfile.mkdtemp(prefix="pano_sweep_victim_")
    stale = time.time() - 2 * printapi.TEMP_MIN_AGE_S
    names = {   # path: (make it old?, expect removed?)
        os.path.join(fv, "a.jpg.part"): (True, True),
        os.path.join(fv, "b.json.part"): (True, True),
        os.path.join(fv, "c.json.prev"): (True, True),
        os.path.join(fv, "c.json"): (True, False),                  # the .prev's sibling
        os.path.join(fv, "orphan.json.prev"): (True, False),        # restored, not removed
        os.path.join(fv, ".proofs", "d.jpg.part"): (True, True),
        os.path.join(fv, "fresh.jpg.part"): (False, False),          # may be in flight
        os.path.join(fv, "keep.jpg"): (True, False),
        os.path.join(fv, "other.part"): (True, False),
        os.path.join(fv, "sub", "e.jpg.part"): (True, False),
        os.path.join(elsewhere, "victim.jpg.part"): (True, False),
    }
    for p, (old, _) in names.items():
        with open(p, "wb") as fh:
            fh.write(b"x")
        if old:
            os.utime(p, (stale, stale))
    linked = False
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(elsewhere, os.path.join(fv, "link"))
            linked = True
        except (ImportError, OSError):
            pass
    else:
        os.symlink(elsewhere, os.path.join(fv, "link"))
        linked = True
    removed = printapi.sweep_temp_files(out)
    assert removed == 4, removed
    for p, (_, gone) in names.items():
        if p.endswith("orphan.json.prev"):
            assert not os.path.exists(p) and os.path.exists(p[:-len(".prev")]), "orphan .prev restored"
            continue
        assert os.path.exists(p) == (not gone), (p, gone)
    if linked:
        assert os.path.exists(os.path.join(elsewhere, "victim.jpg.part"))


def test_sweep_restore_never_overwrites_a_record_placed_meanwhile():
    """Between the sweep's existence check and its move, a publish in another viewer
    may land a new X.json. The restore must then step back, not overwrite it."""
    out = tempfile.mkdtemp(prefix="pano_sweep_race_")
    fv = os.path.join(out, "prints", "from-viewer")
    os.makedirs(fv)
    prev = os.path.join(fv, "x.json.prev")
    rec = prev[:-len(".prev")]
    with open(prev, "wb") as fh:
        fh.write(b"old")
    aged = time.time() - 2 * printapi.TEMP_PREV_GRACE_S
    os.utime(prev, (aged, aged))
    real_exists = os.path.exists

    def racing_exists(p):
        r = real_exists(p)
        if not r and os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(rec)):
            with open(rec, "wb") as fh:            # another viewer's publish lands X.json now
                fh.write(b"new")
        return r

    os.path.exists = racing_exists
    try:
        printapi.sweep_temp_files(out)
    finally:
        os.path.exists = real_exists
    with open(rec, "rb") as fh:
        assert fh.read() == b"new", "the sweep overwrote a record a concurrent publish placed"
    assert os.path.isfile(prev), "the .prev must stay for a later sweep"


def test_lone_surrogate_title_is_stripped_before_it_can_break_the_sidecar():
    req = validate(_good_body(title="\ud800abc\udfff \ue000"))   # surrogates + private use
    assert req["title"] == "abc"


def test_sidecar_failure_publishes_nothing():
    """If the record cannot be written, the print must not appear either."""
    from panolib import _print_cmd
    s = _server()
    fv = os.path.join(s["out"], "prints", "from-viewer")
    os.makedirs(fv, exist_ok=True)
    before = set(os.listdir(fv))
    real = _print_cmd.write_sidecar

    def failing(path, cap, extra):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{partial")
        raise ValueError("cannot encode")

    _print_cmd.write_sidecar = failing
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="nothing published"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
    finally:
        _print_cmd.write_sidecar = real
    assert j["state"] == "error" and "cannot encode" in j["error"], j
    after = set(os.listdir(fv))
    assert after == before, after - before


def test_second_launch_on_a_busy_port_leaves_temp_files_alone():
    """A second `panolib view` on the same port must fail at bind -- BEFORE sweeping."""
    import contextlib
    s = _server()
    inflight = os.path.join(s["out"], "prints", "from-viewer", "inflight.jpg.part")
    os.makedirs(os.path.dirname(inflight), exist_ok=True)
    with open(inflight, "wb") as fh:
        fh.write(b"x")
    try:
        try:
            printapi.serve(s["out"], s["port"])
        except OSError:
            pass
        else:
            raise AssertionError("binding the busy port should fail")
        assert os.path.exists(inflight), "a failed launch must not sweep the live server's files"
    finally:
        with contextlib.suppress(FileNotFoundError):      # #5: keep the assertion's message
            os.remove(inflight)


def test_two_panoramas_with_the_same_folder_name_do_not_share_a_print():
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    paths = []
    for pid in ("test-pano-1", "test-pano-1b"):
        code, body = _req("POST", "/api/print", _good_body(id=pid), tok)
        assert code == 202, (code, body)
        j = _finish(body)
        assert j["state"] == "done", j
        paths.append(j["result"]["path"])
    assert paths[0] != paths[1], paths
    assert "test-pano-1b" in os.path.basename(paths[1])


def _print_final(s, title):
    code, body = _req("POST", "/api/print",
                      _good_body(source="equirect", proof=False, size="5x7", dpi=100, title=title),
                      {"X-Print-Token": s["ctx"].token})
    assert code == 202
    j = _finish(body)
    assert j["state"] == "done", j
    res = j["result"]
    return os.path.normpath(res["path"]), os.path.normpath(os.path.join(s["out"], res["sidecar"].lstrip("/")))


def _mark(path: str) -> bytes:
    """Append a byte so the earlier file differs from what a re-print would write
    (the re-print is byte-identical otherwise, and 'untouched' would be vacuous)."""
    with open(path, "ab") as fh:
        fh.write(b"\n")
    with open(path, "rb") as fh:
        return fh.read()


def _no_temporaries(folder: str, stem: str | None = None) -> None:
    """No .part/.prev in the folder -- scoped to one print's stem when given, so a
    leak from an earlier failed test is reported there, not here."""
    names = [f for f in os.listdir(folder) if f.endswith((".part", ".prev"))]
    if stem is not None:
        names = [f for f in names if f.startswith(os.path.basename(stem))]
    assert not names, names


def _restore_fixture(side: str) -> None:
    """Whatever a failed assertion left behind, put the earlier record back."""
    prev = side + ".prev"
    if os.path.exists(prev):
        if os.path.exists(side):
            os.remove(prev)
        else:
            os.replace(prev, side)


def test_pair_moves_together_when_the_image_is_held_open():
    """Re-printing while the earlier X.jpg is open: the new pair lands on X-2.jpg and
    X-2.json together, and the earlier pair is untouched. (Where the OS allows the
    rename over an open file -- POSIX -- the re-print replaces the pair in place.)"""
    s = _server()
    jpg, side = _print_final(s, "held image")
    old_record = _mark(side)
    old_tries = printapi.REPLACE_TRIES
    printapi.REPLACE_TRIES = 1
    try:
        with open(jpg, "rb"):
            jpg2, side2 = _print_final(s, "held image")
    finally:
        printapi.REPLACE_TRIES = old_tries
        _restore_fixture(side)
    assert os.path.splitext(side2)[0] == os.path.splitext(jpg2)[0], (jpg2, side2)
    assert os.path.isfile(jpg2) and os.path.isfile(side2)
    _no_temporaries(os.path.dirname(jpg), os.path.splitext(jpg)[0])
    if os.name == "nt":
        assert jpg2 == os.path.splitext(jpg)[0] + "-2.jpg", jpg2
        with open(side, "rb") as fh:
            assert fh.read() == old_record, "the earlier record was overwritten"
        assert os.path.isfile(jpg)
    else:
        assert (jpg2, side2) == (jpg, side)
        with open(side, "rb") as fh:
            assert fh.read() != old_record, "the record should have been replaced in place"


def test_pair_moves_together_when_the_record_is_held_open():
    """Re-printing while the earlier X.json is open in an editor: X.jpg must NOT be
    replaced and then withdrawn; the new pair goes to X-2 and the old pair survives."""
    s = _server()
    jpg, side = _print_final(s, "held record")
    old_image = _mark(jpg)
    old_tries = printapi.REPLACE_TRIES
    printapi.REPLACE_TRIES = 1
    try:
        with open(side, "rb"):
            jpg2, side2 = _print_final(s, "held record")
    finally:
        printapi.REPLACE_TRIES = old_tries
        _restore_fixture(side)
    _no_temporaries(os.path.dirname(jpg), os.path.splitext(jpg)[0])
    if os.name == "nt":
        assert os.path.splitext(jpg2)[0] == os.path.splitext(side2)[0] == os.path.splitext(jpg)[0] + "-2"
        with open(jpg, "rb") as fh:
            assert fh.read() == old_image, "the earlier image was overwritten"
        assert os.path.isfile(side)
    else:
        assert (jpg2, side2) == (jpg, side)


def test_nothing_is_published_when_every_name_is_blocked():
    """If no stem can be taken, the folder is exactly as it was and the error names
    the cause. Simulated by making every rename fail as a lock would."""
    s = _server()
    fv = os.path.join(s["out"], "prints", "from-viewer")
    os.makedirs(fv, exist_ok=True)
    before = set(os.listdir(fv))
    real = printapi._try_replace
    printapi._try_replace = lambda src, dst, tries: False
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="all blocked"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
    finally:
        printapi._try_replace = real
    assert j["state"] == "error" and "open in another program" in j["error"], j
    assert set(os.listdir(fv)) == before


def test_image_blocked_restores_the_earlier_record():
    """If the record is placed but the image cannot follow, the earlier record is put
    back exactly and our temporaries vanish -- for every stem tried."""
    s = _server()
    jpg, side = _print_final(s, "restore me")
    old_record = _mark(side)
    stale = time.time() - 2 * printapi.TEMP_MIN_AGE_S
    os.utime(side, (stale, stale))                 # an earlier record printed long ago
    fv = os.path.dirname(jpg)
    before = set(os.listdir(fv))
    real = printapi._try_replace

    def image_blocked(src, dst, tries):
        if dst.endswith(".jpg"):
            return False
        return real(src, dst, tries)

    printapi._try_replace = image_blocked
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="restore me"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
        assert j["state"] == "error", j
        after = set(os.listdir(fv))
        assert after == before, (after - before, before - after)
        with open(side, "rb") as fh:
            assert fh.read() == old_record, "the earlier record was not restored byte for byte"
        assert abs(os.stat(side).st_mtime - stale) < 2, "the restored record must keep its own date"
    finally:
        printapi._try_replace = real
        _restore_fixture(side)


def test_failed_restore_of_the_earlier_record_is_an_error_not_a_success():
    """If the earlier record cannot be put back, the job must say so and name the
    .prev file -- never report 'done' with a broken earlier pair -- and the next
    server start must restore the orphaned record."""
    s = _server()
    jpg, side = _print_final(s, "stuck prev")
    old_record = _mark(side)
    stale = time.time() - 2 * printapi.TEMP_MIN_AGE_S
    os.utime(side, (stale, stale))                 # an earlier record printed long ago
    real = printapi._try_replace

    def stuck(src, dst, tries):
        if dst.endswith(".jpg") or src.endswith(".prev"):
            return False
        return real(src, dst, tries)

    printapi._try_replace = stuck
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="stuck prev"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
    finally:
        printapi._try_replace = real
    try:
        assert j["state"] == "error" and ".json.prev" in j["error"], j
        prev = side + ".prev"
        assert os.path.isfile(prev) and not os.path.exists(side)
        assert os.path.isfile(jpg)
        _no_temporaries(os.path.dirname(jpg), os.path.splitext(jpg)[0] + ".jpg")
        # the set-aside copy was stamped when it was made (the earlier record was old),
        # so a concurrent sweep's age rule treats it as in flight
        assert os.stat(prev).st_mtime > time.time() - 60, "the .prev must carry a fresh stamp"
        # ... and while it is that fresh, the sweep leaves it alone
        printapi.sweep_temp_files(s["out"])
        assert os.path.isfile(prev) and not os.path.exists(side), \
            "a .prev younger than TEMP_PREV_GRACE_S must be left alone by the sweep"
        # once a publish can no longer be running, a .prev without its .json goes back
        aged = time.time() - 2 * printapi.TEMP_PREV_GRACE_S
        os.utime(prev, (aged, aged))
        printapi.sweep_temp_files(s["out"])
        assert os.path.isfile(side) and not os.path.exists(prev)
        with open(side, "rb") as fh:
            assert fh.read() == old_record
    finally:
        _restore_fixture(side)


def test_record_placement_failure_restores_the_earlier_record_and_moves_on():
    """If our record cannot take X.json (locked), the earlier record goes back exactly
    and the pair lands on X-2."""
    s = _server()
    jpg, side = _print_final(s, "record blocked")
    old_record = _mark(side)
    real = printapi._try_replace
    base = os.path.splitext(jpg)[0]

    def record_blocked(src, dst, tries):
        if src.endswith(".json.part") and dst == base + ".json":
            return False
        return real(src, dst, tries)

    stale = time.time() - 2 * printapi.TEMP_MIN_AGE_S
    os.utime(side, (stale, stale))
    printapi._try_replace = record_blocked
    try:
        jpg2, side2 = _print_final(s, "record blocked")
    finally:
        printapi._try_replace = real
        # the fixture is put back for later tests whatever happened -- but what the
        # job left behind is judged first, or the restore would mask a leak
        leaked_prev = os.path.exists(side + ".prev")
        restored = os.path.exists(side)
        _restore_fixture(side)
    assert (jpg2, side2) == (base + "-2.jpg", base + "-2.json"), (jpg2, side2)
    assert restored and not leaked_prev, "the earlier record was left as .prev"
    with open(side, "rb") as fh:
        assert fh.read() == old_record, "the earlier record was not restored"
    assert abs(os.stat(side).st_mtime - stale) < 2, "the restored record must keep its own date"
    _no_temporaries(os.path.dirname(jpg), base)


def test_record_placement_failure_with_stuck_prev_is_an_error():
    s = _server()
    jpg, side = _print_final(s, "record blocked, prev stuck")
    old_record = _mark(side)
    real = printapi._try_replace
    base = os.path.splitext(jpg)[0]

    def stuck(src, dst, tries):
        if (src.endswith(".json.part") and dst == base + ".json") or src.endswith(".prev"):
            return False
        return real(src, dst, tries)

    printapi._try_replace = stuck
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="record blocked, prev stuck"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
        assert j["state"] == "error" and ".json.prev" in j["error"], j
        assert os.path.isfile(side + ".prev") and not os.path.exists(side) and os.path.isfile(jpg)
        _no_temporaries(os.path.dirname(jpg), base + ".jpg")
    finally:
        printapi._try_replace = real
        _restore_fixture(side)                      # put the fixture back for later tests
    with open(side, "rb") as fh:
        assert fh.read() == old_record


def test_reprint_replaces_the_pair_in_place_and_leaves_no_prev():
    s = _server()
    jpg, side = _print_final(s, "plain reprint")
    old_record = _mark(side)
    jpg2, side2 = _print_final(s, "plain reprint")
    assert (jpg2, side2) == (jpg, side)
    with open(side, "rb") as fh:
        assert fh.read() != old_record, "the record should have been replaced"
    _no_temporaries(os.path.dirname(jpg), os.path.splitext(jpg)[0])


def test_vanished_file_during_the_undo_is_still_reported_cleanly():
    """The image is blocked and our just-placed record disappears before it can be
    moved back (a quarantine, a deletion, another viewer's sweep). The undo must not
    raise on its own: the earlier record still goes back, the temporaries still go,
    and the message is ours, not a WinError path pair."""
    s = _server()
    jpg, side = _print_final(s, "vanishing record")
    old_record = _mark(side)
    stale = time.time() - 2 * printapi.TEMP_MIN_AGE_S
    os.utime(side, (stale, stale))                 # an earlier record printed long ago
    real = printapi._try_replace

    def hostile(src, dst, tries):
        if dst.endswith(".jpg"):
            return False                            # image blocked under every stem
        if src.endswith(".json") and dst.endswith(".json.part"):
            os.remove(src)                          # our placed record vanishes
        return real(src, dst, tries)

    printapi._try_replace = hostile
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="vanishing record"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
        assert j["state"] == "error", j
        assert "vanished" in j["error"] and "WinError" not in j["error"], j["error"]
        assert not j["error"].startswith(("_", "RuntimeError")), j["error"]   # the panel shows our words
        with open(side, "rb") as fh:
            assert fh.read() == old_record, "the earlier record was not restored"
        assert abs(os.stat(side).st_mtime - stale) < 2, "the restored record must keep its own date"
        assert os.path.isfile(jpg)
        _no_temporaries(os.path.dirname(jpg), os.path.splitext(jpg)[0])
    finally:
        printapi._try_replace = real
        _restore_fixture(side)


def test_record_temp_is_removed_even_when_the_image_temp_is_locked():
    s = _server()
    fv = os.path.join(s["out"], "prints", "from-viewer")
    os.makedirs(fv, exist_ok=True)
    before = set(os.listdir(fv))
    real_try, real_rm = printapi._try_replace, printapi._remove_with_backoff
    printapi._try_replace = lambda src, dst, tries: False
    printapi._remove_with_backoff = lambda p: False if p.endswith(".jpg.part") else real_rm(p)
    try:
        code, body = _req("POST", "/api/print",
                          _good_body(source="equirect", proof=False, size="5x7", dpi=100,
                                     title="image temp locked"),
                          {"X-Print-Token": s["ctx"].token})
        assert code == 202
        j = _finish(body)
    finally:
        printapi._try_replace, printapi._remove_with_backoff = real_try, real_rm
    assert j["state"] == "error" and "temporary file is locked" in j["error"], j
    new = set(os.listdir(fv)) - before
    assert all(f.endswith(".jpg.part") for f in new) and len(new) == 1, new
    for f in new:
        os.remove(os.path.join(fv, f))


def test_open_destination_falls_back_to_a_sibling_name():
    """Windows refuses to rename over a file another program holds open. The print must
    still be saved -- under a numbered sibling -- and the .part must not survive."""
    d = tempfile.mkdtemp(prefix="pano_replace_")
    dest = os.path.join(d, "print.jpg")
    with open(dest, "wb") as fh:
        fh.write(b"old")
    part = dest + ".part"
    with open(part, "wb") as fh:
        fh.write(b"new")
    old_tries = printapi.REPLACE_TRIES
    printapi.REPLACE_TRIES = 1
    try:
        if os.name == "nt":
            with open(dest, "rb"):                    # hold it open, as a viewer would
                final, _ = printapi._publish_pair(part, None, dest)
            assert final == os.path.join(d, "print-2.jpg"), final
        else:
            final, _ = printapi._publish_pair(part, None, dest)
            assert final == dest
    finally:
        printapi.REPLACE_TRIES = old_tries
    with open(final, "rb") as fh:
        assert fh.read() == b"new"
    assert not os.path.exists(part)


def test_distinct_requests_get_distinct_filenames():
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    results = []
    for title in ("One", "Two"):
        code, body = _req("POST", "/api/print",
                          _good_body(proof=False, size="5x7", dpi=100, title=title), tok)
        assert code == 202
        for _ in range(300):
            _, j = _req("GET", f"/api/jobs/{body['job']}")
            if j["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert j["state"] == "done", j
        results.append(j["result"]["path"])
    assert results[0] != results[1], "a different title must not overwrite the first print"
    assert all(os.path.isfile(p) for p in results)


def test_too_many_live_jobs_is_429_and_live_jobs_are_never_evicted():
    """With the render slot held, jobs queue; past MAX_LIVE_JOBS the server says 429
    instead of accepting work it cannot start, and a queued job stays pollable."""
    s = _server()
    tok = {"X-Print-Token": s["ctx"].token}
    ctx = s["ctx"]
    live = []
    for _ in range(300):                           # let any earlier test's job drain first
        with ctx.lock:
            live = [j for j in ctx.jobs.values() if j["state"] in ("queued", "running")]
        if not live:
            break
        time.sleep(0.1)
    assert not live, "earlier jobs still live"
    assert ctx.render_slot.acquire(timeout=30)     # nothing can render until released
    try:
        # Fill the table with FINISHED records stamped in the future. Correct eviction
        # drops only finished records, so the live jobs survive. The old behaviour --
        # evict the oldest record regardless of state -- would drop the live jobs,
        # because they are older than these.
        with ctx.lock:
            for n in range(printapi.MAX_JOBS - 1):
                ctx.jobs[f"filler{n:03d}"] = {"state": "done", "progress": 1.0,
                                              "created": time.time() + 1000 + n,
                                              "result": {}, "error": None}
        accepted = []
        for _ in range(printapi.MAX_LIVE_JOBS):
            code, body = _req("POST", "/api/print", _good_body(), tok)
            assert code == 202, (code, body)
            accepted.append(body["job"])
        code, body = _req("POST", "/api/print", _good_body(), tok)
        assert code == 429, (code, body)
        with ctx.lock:
            assert len(ctx.jobs) <= printapi.MAX_JOBS
            assert all(jid in ctx.jobs for jid in accepted), "a live job was evicted"
        for jid in accepted:
            code, snap = _req("GET", f"/api/jobs/{jid}")
            assert code == 200 and snap["state"] in ("queued", "running"), snap
    finally:
        ctx.render_slot.release()
    for jid in accepted:                           # and they all finish once released
        for _ in range(600):
            _, j = _req("GET", f"/api/jobs/{jid}")
            if j["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert j["state"] == "done", j


# ------------------------------------------------------------------ a real render

def test_proof_renders_and_is_served():
    s = _server()
    code, body = _req("POST", "/api/print", _good_body(),
                      {"X-Print-Token": s["ctx"].token})
    assert code == 202
    for _ in range(300):
        _, j = _req("GET", f"/api/jobs/{body['job']}")
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["state"] == "done", j
    res = j["result"]
    assert os.path.isfile(res["path"]), res
    assert res["url"].startswith("/prints/from-viewer/")
    with urllib.request.urlopen(s["base"] + res["url"], timeout=10) as r:
        assert r.status == 200
    with Image.open(res["path"]) as im:
        assert im.size == (res["width"], res["height"])
    assert res["proof"] is True and res["sidecar"] is None


def test_unknown_job_is_404():
    assert _req("GET", "/api/jobs/nope")[0] == 404


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {fn.__name__}: {exc.__class__.__name__}: {exc}")
    if _SERVER:
        _SERVER["httpd"].shutdown()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
