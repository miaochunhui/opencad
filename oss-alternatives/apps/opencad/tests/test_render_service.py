"""CPU, input and loopback HTTP controls; hardware is tested by gpu_render_probe."""

import base64
import copy
import hashlib
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import render_service as r


def triangle(**kwargs):
    value = {"vertices": [[-0.75, -0.75, 0], [0.75, -0.75, 0], [0, 0.75, 0]],
             "triangles": [[0, 1, 2]], "colors": [[20, 80, 200]],
             "width": 64, "height": 64, "backend": "cpu"}
    value.update(kwargs)
    return value


class InputTests(unittest.TestCase):
    def bad(self, **kwargs):
        with self.assertRaises(r.InvalidInput):
            r.validate_request(triangle(**kwargs))

    def test_empty_mesh(self):
        self.bad(vertices=[], triangles=[])

    def test_nonfinite(self):
        for value in (float("nan"), float("inf"), True, "0", 10 ** 400):
            self.bad(vertices=[[value, 0, 0], [1, 0, 0], [0, 1, 0]])

    def test_outside_clip(self):
        self.bad(vertices=[[2, 0, 0], [1, 0, 0], [0, 1, 0]])

    def test_invalid_index(self):
        for tri in ([0, 1, 3], [0, 1, 1], [False, 1, 2], [0, 1]):
            self.bad(triangles=[tri])

    def test_degenerate(self):
        self.bad(vertices=[[0, 0, 0], [0.1, 0.1, 0.1], [0.2, 0.2, 0.2]])

    def test_dimensions(self):
        for dimension in (0, 15, 1025, True, 32.5):
            self.bad(width=dimension)
        self.bad(width=1024, height=1024)

    def test_counts(self):
        self.bad(vertices=[[0, 0, 0]] * (r.MAX_VERTICES + 1))
        self.bad(triangles=[[0, 1, 2]] * (r.MAX_TRIANGLES + 1))

    def test_raster_work_bound(self):
        self.bad(width=512, height=512, triangles=[[0, 1, 2]] * 200,
                 colors=[[1, 2, 3]] * 200)

    def test_colors(self):
        for colors in ([], [[0, 1]], [[0, 1, 256]], [[0, 1, True]]):
            self.bad(colors=colors)

    def test_unknown_and_backend(self):
        self.bad(shader="user shader")
        self.bad(backend="auto")
        self.bad(allow_cpu_fallback=1)

    def test_json_controls(self):
        for body in (b'{"vertices":[],"vertices":[]}', b'{"x":NaN}', b"\xff", b"{"):
            with self.assertRaises(r.InvalidInput):
                r.decode_request(body)
        with self.assertRaises(r.InvalidInput):
            r.decode_request(b" " * (r.MAX_BODY + 1))

    def test_projection(self):
        original = {"vertices_mm": [[0, 0, 0], [100, 0, 0], [0, 100, 1]],
                    "triangles": [[0, 1, 2]]}
        frozen = copy.deepcopy(original)
        for view in ("front", "top", "right", "isometric"):
            value = r.project_mesh(original, view=view)
            r.validate_request(value)
        self.assertEqual(original, frozen)
        with self.assertRaises(r.InvalidInput):
            r.project_mesh(original, view="arbitrary")


class RenderTests(unittest.TestCase):
    def test_software_and_wrong_hardware_rejected(self):
        for renderer in ("SwiftShader", "llvmpipe", "softpipe", "ANGLE NVIDIA RTX 3080",
                         "NVIDIA RTX 3080 software", "NVIDIA RTX 4090", ""):
            with self.subTest(renderer=renderer), self.assertRaises(r.GPUUnavailable):
                r.require_hardware({"vendor": "NVIDIA Corporation", "renderer": renderer})
        r.require_hardware({"vendor": "NVIDIA Corporation", "renderer": "NVIDIA GeForce RTX 3080/PCIe/SSE2"})

    def test_cross_instance_gpu_busy(self):
        # Call the lock gate without requiring any optional graphics dependency.
        renderer = object.__new__(r.EGLRenderer)
        with r.GPU_LOCK, self.assertRaises(r.RenderBusy):
            renderer.render(None)

    def test_analytic_triangle(self):
        mesh, _, _ = r.validate_request(triangle())
        pixels = r.cpu_render(mesh)
        self.assertEqual(len(pixels), 64 * 64 * 4)
        self.assertEqual(pixels[(32 * 64 + 32) * 4: (32 * 64 + 32) * 4 + 4], bytes((20, 80, 200, 255)))
        self.assertEqual(pixels[:4], bytes(r.BACKGROUND))
        self.assertEqual(pixels[-4:], bytes(r.BACKGROUND))

    def test_depth(self):
        value = triangle(vertices=[[-0.75, -0.75, 0.5], [0.75, -0.75, 0.5],
                                   [0, 0.75, 0.5], [-0.75, -0.75, -0.5],
                                   [0.75, -0.75, -0.5], [0, 0.75, -0.5]],
                         triangles=[[0, 1, 2], [3, 4, 5]], colors=[[255, 0, 0], [0, 255, 0]])
        mesh, _, _ = r.validate_request(value)
        pixels = r.cpu_render(mesh)
        self.assertEqual(pixels[(32 * 64 + 32) * 4:(32 * 64 + 32) * 4 + 4], b"\x00\xff\x00\xff")

    def test_far_depth_plane_matches_less_test(self):
        mesh, _, _ = r.validate_request(triangle(
            vertices=[[-0.75, -0.75, 1], [0.75, -0.75, 1], [0, 0.75, 1]]))
        self.assertEqual(r.cpu_render(mesh), bytes(r.BACKGROUND) * (64 * 64))

    def test_repeat_digest_and_immutable(self):
        value = triangle()
        before = copy.deepcopy(value)
        service = r.RenderService()
        a, b = service.render(value), service.render(value)
        self.assertEqual(value, before)
        for key in ("geometry_sha256", "frame_sha256", "png_sha256", "image_png_base64"):
            self.assertEqual(a[key], b[key])
        png = base64.b64decode(a["image_png_base64"], validate=True)
        self.assertEqual(hashlib.sha256(png).hexdigest(), a["png_sha256"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        changed = service.render(triangle(colors=[[255, 0, 0]]))
        self.assertNotEqual(a["frame_sha256"], changed["frame_sha256"])
        self.assertNotEqual(a["geometry_sha256"], changed["geometry_sha256"])

    def test_disabled_gpu_loud_and_explicit_fallback(self):
        service = r.RenderService()
        with self.assertRaisesRegex(r.GPUUnavailable, "disabled"):
            service.render(triangle(backend="gpu"))
        with self.assertLogs("opencad.render", level="WARNING"):
            value = service.render(triangle(backend="gpu", allow_cpu_fallback=True))
        self.assertEqual(value["status"], "CPU_FALLBACK")
        self.assertFalse(value["renderer"]["hardware"])
        self.assertIn("disabled", value["fallback_reason"])

    def test_missing_backend_control(self):
        service = r.RenderService(enable_gpu=True)
        with patch.object(r, "EGLRenderer", side_effect=r.GPUUnavailable("missing EGL")):
            with self.assertRaisesRegex(r.GPUUnavailable, "missing EGL"):
                service.render(triangle(backend="gpu"))
            with self.assertLogs("opencad.render", level="WARNING"):
                value = service.render(triangle(backend="gpu", allow_cpu_fallback=True))
            self.assertEqual(value["fallback_reason"], "missing EGL")

    def test_render_error_never_silently_falls_back(self):
        service = r.RenderService(enable_gpu=True)
        with patch.object(r, "EGLRenderer", side_effect=r.RenderError("shader failed")):
            with self.assertRaisesRegex(r.RenderError, "shader failed"):
                service.render(triangle(backend="gpu", allow_cpu_fallback=True))

    def test_busy(self):
        service = r.RenderService()
        with service._lock:
            with self.assertRaises(r.RenderBusy):
                service.render(triangle())
        self.assertEqual(service.render(triangle())["backend"], "cpu")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = r.make_http_server(r.RenderService())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, body, path="/render"):
        req = urllib.request.Request(self.base + path, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_real_post(self):
        status, value = self.request(json.dumps(triangle()).encode())
        self.assertEqual(status, 200)
        self.assertEqual(value["backend"], "cpu")
        self.assertFalse(value["renderer"]["hardware"])

    def test_real_error_and_fallback(self):
        status, value = self.request(json.dumps(triangle(backend="gpu")).encode())
        self.assertEqual(status, 503)
        self.assertEqual(value["error"], "gpu_unavailable")
        status, value = self.request(json.dumps(triangle(backend="gpu", allow_cpu_fallback=True)).encode())
        self.assertEqual(status, 200)
        self.assertEqual(value["status"], "CPU_FALLBACK")

    def test_empty_unknown_and_invalid(self):
        for body in (b"{}", b'{"x":1}', b'{"x":1,"x":2}', b""):
            status, value = self.request(body)
            self.assertEqual(status, 400)
            self.assertEqual(value["error"], "invalid_render_input")
        self.assertEqual(self.request(b"{}", "/other")[0], 404)

    def test_health_not_availability_proof(self):
        with self.opener.open(self.base + "/health") as response:
            value = json.load(response)
        self.assertEqual(value["gpu_availability"], "NOT_PROBED")
        self.assertFalse(value["production_ready"])


if __name__ == "__main__":
    unittest.main()
