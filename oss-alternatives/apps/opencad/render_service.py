"""Bounded, stateless offscreen mesh renderer; no browser, files or privilege calls.

The integration owner may call RenderService.render() or use the independent
loopback HTTP adapter (POST /render). Input vertices are clip-space XYZ in
[-1, 1]; project_mesh() adapts the CAD kernel's vertices_mm before submission.
GPU mode uses EGLDevice + GLES2 and requires the explicitly authorized RTX3080.
CPU rasterization is independent and never described as hardware rendering.
"""

from __future__ import annotations

import argparse
import base64
import ctypes as C
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import math
import os
from pathlib import Path
import socket
import struct
import threading
import time
import zlib

MAX_BODY = 1_048_576
MAX_VERTICES = 8192
MAX_TRIANGLES = 4096
MAX_PIXELS = 262144
MAX_PIXEL_WORK = 16_777_216
BACKGROUND = (245, 246, 248, 255)
DEFAULT_COLOR = (32, 140, 200)
LOG = logging.getLogger("opencad.render")
GPU_LOCK = threading.Lock()


class RenderError(RuntimeError):
    code = "render_failed"
    http_status = 500


class InvalidInput(RenderError):
    code = "invalid_render_input"
    http_status = 400


class GPUUnavailable(RenderError):
    code = "gpu_unavailable"
    http_status = 503


class RenderBusy(RenderError):
    code = "renderer_busy"
    http_status = 503


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise InvalidInput(f"{label} must be an integer in [{low}, {high}]")
    return value


def _point(value, bound=1.0):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise InvalidInput("each vertex must contain exactly three coordinates")
    if any(type(x) not in (float, int) or abs(x) > bound or not math.isfinite(x)
           for x in value):
        raise InvalidInput(f"vertex coordinates must be finite, abs <= {bound}")
    return tuple(float(x) for x in value)


@dataclass(frozen=True)
class Mesh:
    vertices: tuple
    triangles: tuple
    colors: tuple
    width: int
    height: int
    pixel_work: int
    geometry_sha256: str


def validate_request(payload):
    if type(payload) is not dict:
        raise InvalidInput("request must be an object")
    allowed = {"vertices", "triangles", "colors", "width", "height",
               "backend", "allow_cpu_fallback"}
    if payload.keys() - allowed:
        raise InvalidInput("unknown request fields")
    backend = payload.get("backend", "cpu")
    if backend not in ("cpu", "gpu"):
        raise InvalidInput("backend must be cpu or gpu")
    fallback = payload.get("allow_cpu_fallback", False)
    if type(fallback) is not bool:
        raise InvalidInput("allow_cpu_fallback must be boolean")
    width = _integer(payload.get("width", 256), 16, 1024, "width")
    height = _integer(payload.get("height", 256), 16, 1024, "height")
    if width * height > MAX_PIXELS:
        raise InvalidInput("frame pixel budget exceeded")
    vertices = payload.get("vertices")
    triangles = payload.get("triangles")
    if type(vertices) is not list or not 3 <= len(vertices) <= MAX_VERTICES:
        raise InvalidInput("nonempty bounded vertices array required")
    vertices = tuple(_point(p) for p in vertices)
    if type(triangles) is not list or not 1 <= len(triangles) <= MAX_TRIANGLES:
        raise InvalidInput("nonempty bounded triangles array required")
    checked, work = [], 0
    for tri in triangles:
        if type(tri) is not list or len(tri) != 3:
            raise InvalidInput("each triangle must contain three indices")
        ids = tuple(_integer(i, 0, len(vertices) - 1, "triangle index") for i in tri)
        if len(set(ids)) != 3:
            raise InvalidInput("triangle indices must be distinct")
        a, b, c = (vertices[i] for i in ids)
        cross = tuple((b[(i + 1) % 3] - a[(i + 1) % 3]) *
                      (c[(i + 2) % 3] - a[(i + 2) % 3]) -
                      (b[(i + 2) % 3] - a[(i + 2) % 3]) *
                      (c[(i + 1) % 3] - a[(i + 1) % 3]) for i in range(3))
        if sum(x * x for x in cross) < 1e-20:
            raise InvalidInput("degenerate triangle")
        # Budget the same bounded raster footprint for both backends.
        xs = [(v[0] + 1) * width / 2 for v in (a, b, c)]
        ys = [(1 - v[1]) * height / 2 for v in (a, b, c)]
        work += (min(width, math.ceil(max(xs))) - max(0, math.floor(min(xs)))) * (
            min(height, math.ceil(max(ys))) - max(0, math.floor(min(ys))))
        if work > MAX_PIXEL_WORK:
            raise InvalidInput("triangle raster work budget exceeded")
        checked.append(ids)
    raw_colors = payload.get("colors", [list(DEFAULT_COLOR)] * len(checked))
    if type(raw_colors) is not list or len(raw_colors) != len(checked):
        raise InvalidInput("colors must have one RGB triple per triangle")
    colors = []
    for rgb in raw_colors:
        if type(rgb) is not list or len(rgb) != 3:
            raise InvalidInput("each color must contain three bytes")
        colors.append(tuple(_integer(x, 0, 255, "color") for x in rgb))
    canonical = {"vertices": vertices, "triangles": checked, "colors": colors,
                 "width": width, "height": height}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return Mesh(vertices, tuple(checked), tuple(colors), width, height, work, digest), backend, fallback


def project_mesh(mesh, *, view="isometric"):
    """Project bounded world-space CAD vertices; preserve triangle indices.

    Orthographic auto-fit only, not a geometry kernel or perspective camera.
    Back/edge-on faces remain valid 3D triangles and may cover zero pixels.
    """
    if view not in ("front", "top", "right", "isometric"):
        raise InvalidInput("unsupported orthographic view")
    raw = mesh.get("vertices_mm") if type(mesh) is dict else None
    if type(raw) is not list or not 3 <= len(raw) <= MAX_VERTICES:
        raise InvalidInput("bounded vertices_mm required")
    points = [_point(p, 1_000_000) for p in raw]
    if view == "isometric":
        projected = [((x - y) / math.sqrt(2), (x + y - 2 * z) / math.sqrt(6),
                      (x + y + z) / math.sqrt(3)) for x, y, z in points]
    elif view == "top":
        projected = [(x, y, -z) for x, y, z in points]
    elif view == "right":
        projected = [(y, z, -x) for x, y, z in points]
    else:
        projected = [(x, z, -y) for x, y, z in points]
    low = [min(p[i] for p in projected) for i in range(3)]
    high = [max(p[i] for p in projected) for i in range(3)]
    scale = max(high[i] - low[i] for i in range(3))
    if scale <= 1e-12:
        raise InvalidInput("zero extent mesh")
    result = {"vertices": [[(p[i] - (low[i] + high[i]) / 2) * 1.6 / scale
                             for i in range(3)] for p in projected],
              "triangles": mesh.get("triangles")}
    if "colors" in mesh:
        result["colors"] = mesh["colors"]
    return result


def cpu_render(mesh):
    """Independent pixel-center barycentric golden, with a depth buffer."""
    width, height = mesh.width, mesh.height
    pixels = bytearray(bytes(BACKGROUND) * (width * height))
    depth = [1.0] * (width * height)
    screen = [((x + 1) * width / 2, (1 - y) * height / 2, z)
              for x, y, z in mesh.vertices]
    for ids, rgb in zip(mesh.triangles, mesh.colors):
        a, b, c = [screen[i] for i in ids]
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-12:
            continue  # A valid 3D face can be exactly edge-on.
        x0 = max(0, math.floor(min(a[0], b[0], c[0])))
        x1 = min(width, math.ceil(max(a[0], b[0], c[0])))
        y0 = max(0, math.floor(min(a[1], b[1], c[1])))
        y1 = min(height, math.ceil(max(a[1], b[1], c[1])))
        color = bytes((*rgb, 255))
        for y in range(y0, y1):
            for x in range(x0, x1):
                px, py = x + 0.5, y + 0.5
                u = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / den
                v = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / den
                w = 1 - u - v
                if min(u, v, w) < -1e-12:
                    continue
                z = a[2] + v * (b[2] - a[2]) + w * (c[2] - a[2])
                index = y * width + x
                if z < depth[index]:
                    depth[index] = z
                    pixels[index * 4:index * 4 + 4] = color
    return bytes(pixels)


def _fn(library, name, result, *args):
    fn = getattr(library, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


def require_hardware(info):
    text = (info.get("vendor", "") + " " + info.get("renderer", "")).lower()
    if ("nvidia" not in text or "rtx 3080" not in text or
            any(word in text for word in ("swiftshader", "llvmpipe", "softpipe", "software", "angle"))):
        raise GPUUnavailable(f"authorized RTX3080 hardware renderer required; observed={info}")


class EGLRenderer:
    """Per-request headless EGLDevice context, no X server or ambient DISPLAY."""

    def __init__(self, cuda_device=0):
        self.cuda_device = _integer(cuda_device, 0, 63, "cuda_device")
        try:
            self.egl = C.CDLL("libEGL.so.1")
            self.gl = C.CDLL("libGLESv2.so.2")
        except OSError as exc:
            raise GPUUnavailable(f"system EGL/GLES library unavailable: {exc}") from exc
        p, i, u = C.c_void_p, C.c_int, C.c_uint
        _fn(self.egl, "eglGetProcAddress", p, C.c_char_p)
        _fn(self.egl, "eglGetError", u)
        _fn(self.egl, "eglInitialize", u, p, C.POINTER(i), C.POINTER(i))
        _fn(self.egl, "eglBindAPI", u, u)
        _fn(self.egl, "eglChooseConfig", u, p, C.POINTER(i), C.POINTER(p), i, C.POINTER(i))
        _fn(self.egl, "eglCreatePbufferSurface", p, p, p, C.POINTER(i))
        _fn(self.egl, "eglCreateContext", p, p, p, p, C.POINTER(i))
        _fn(self.egl, "eglMakeCurrent", u, p, p, p, p)
        _fn(self.egl, "eglDestroyContext", u, p, p)
        _fn(self.egl, "eglDestroySurface", u, p, p)
        _fn(self.egl, "eglTerminate", u, p)
        self.query_devices = self._extension("eglQueryDevicesEXT", u, i, C.POINTER(p), C.POINTER(i))
        self.query_string = self._extension("eglQueryDeviceStringEXT", C.c_char_p, p, i)
        self.query_attribute = self._extension("eglQueryDeviceAttribEXT", u, p, i, C.POINTER(C.c_ssize_t))
        self.platform_display = self._extension("eglGetPlatformDisplayEXT", p, u, p, C.POINTER(i))
        f = C.c_float
        signatures = {
            "glGetString": (C.c_char_p, u), "glGetError": (u,),
            "glViewport": (None, i, i, i, i), "glClearColor": (None, f, f, f, f),
            "glClearDepthf": (None, f), "glClear": (None, u),
            "glEnable": (None, u), "glDisable": (None, u), "glDepthFunc": (None, u),
            "glCreateShader": (u, u), "glShaderSource": (None, u, i, C.POINTER(C.c_char_p), C.POINTER(i)),
            "glCompileShader": (None, u), "glGetShaderiv": (None, u, u, C.POINTER(i)),
            "glGetShaderInfoLog": (None, u, i, C.POINTER(i), p), "glDeleteShader": (None, u),
            "glCreateProgram": (u,), "glAttachShader": (None, u, u),
            "glBindAttribLocation": (None, u, u, C.c_char_p), "glLinkProgram": (None, u),
            "glGetProgramiv": (None, u, u, C.POINTER(i)),
            "glGetProgramInfoLog": (None, u, i, C.POINTER(i), p),
            "glUseProgram": (None, u), "glDeleteProgram": (None, u),
            "glGenBuffers": (None, i, C.POINTER(u)), "glBindBuffer": (None, u, u),
            "glBufferData": (None, u, C.c_ssize_t, p, u),
            "glDeleteBuffers": (None, i, C.POINTER(u)),
            "glEnableVertexAttribArray": (None, u),
            "glVertexAttribPointer": (None, u, i, u, C.c_ubyte, i, p),
            "glDrawArrays": (None, u, i, i), "glFinish": (None,),
            "glPixelStorei": (None, u, i),
            "glReadPixels": (None, i, i, i, i, u, u, p),
        }
        for name, signature in signatures.items():
            _fn(self.gl, name, *signature)

    def _extension(self, name, result, *args):
        address = self.egl.eglGetProcAddress(name.encode())
        if not address:
            raise GPUUnavailable(f"required EGL extension function missing: {name}")
        return C.CFUNCTYPE(result, *args)(address)

    def _egl_check(self, value, operation):
        if not value:
            raise GPUUnavailable(f"{operation} failed EGL=0x{self.egl.eglGetError():04x}")
        return value

    def _device(self):
        devices = (C.c_void_p * 64)()
        count = C.c_int()
        self._egl_check(self.query_devices(64, devices, C.byref(count)), "eglQueryDevicesEXT")
        if not 0 < count.value <= 64:
            raise GPUUnavailable(f"EGL device count invalid: {count.value}")
        for device in devices[:count.value]:
            extensions = (self.query_string(device, 0x3055) or b"").decode("ascii", "replace")
            if "EGL_NV_device_cuda" not in extensions.split():
                continue
            ordinal = C.c_ssize_t(-1)
            self._egl_check(self.query_attribute(device, 0x323A, C.byref(ordinal)),
                            "eglQueryDeviceAttribEXT(CUDA_DEVICE)")
            if ordinal.value == self.cuda_device:
                return device
        raise GPUUnavailable(f"no NVIDIA EGL device for CUDA ordinal {self.cuda_device}")

    def _shader(self, kind, text):
        shader = self.gl.glCreateShader(kind)
        source = C.c_char_p(text)
        self.gl.glShaderSource(shader, 1, C.byref(source), None)
        self.gl.glCompileShader(shader)
        ok = C.c_int()
        self.gl.glGetShaderiv(shader, 0x8B81, C.byref(ok))
        if not ok.value:
            log = C.create_string_buffer(4096)
            self.gl.glGetShaderInfoLog(shader, len(log), None, log)
            self.gl.glDeleteShader(shader)
            raise RenderError(f"fixed shader compile failed: {log.value.decode('utf8', 'replace')}")
        return shader

    def render(self, mesh):
        # EGL displays are shared within a process, including across service instances.
        if not GPU_LOCK.acquire(blocking=False):
            raise RenderBusy("one EGL request already active in this process")
        try:
            return self._render(mesh)
        finally:
            GPU_LOCK.release()

    def _render(self, mesh):
        egl, gl = self.egl, self.gl
        display = self._egl_check(self.platform_display(0x313F, self._device(), None),
                                  "eglGetPlatformDisplayEXT(DEVICE)")
        surface = context = None
        initialized = False
        current = False
        shaders, program, buffer = [], 0, C.c_uint()
        primary = None
        try:
            major, minor = C.c_int(), C.c_int()
            self._egl_check(egl.eglInitialize(display, C.byref(major), C.byref(minor)), "eglInitialize")
            initialized = True
            self._egl_check(egl.eglBindAPI(0x30A0), "eglBindAPI(GLES)")
            attrs = (C.c_int * 15)(0x3033, 1, 0x3040, 4, 0x3024, 8, 0x3023, 8,
                                  0x3022, 8, 0x3021, 8, 0x3025, 24, 0x3038)
            config, count = C.c_void_p(), C.c_int()
            self._egl_check(egl.eglChooseConfig(display, attrs, C.byref(config), 1, C.byref(count)),
                            "eglChooseConfig")
            if count.value != 1:
                raise GPUUnavailable("no RGBA8/depth24 EGL pbuffer config")
            size = (C.c_int * 5)(0x3057, mesh.width, 0x3056, mesh.height, 0x3038)
            surface = self._egl_check(egl.eglCreatePbufferSurface(display, config, size), "eglCreatePbufferSurface")
            ctx_attrs = (C.c_int * 3)(0x3098, 2, 0x3038)
            context = self._egl_check(egl.eglCreateContext(display, config, None, ctx_attrs), "eglCreateContext")
            self._egl_check(egl.eglMakeCurrent(display, surface, surface, context), "eglMakeCurrent")
            current = True
            info = {name: (gl.glGetString(enum) or b"").decode("utf8", "replace")
                    for name, enum in (("vendor", 0x1F00), ("renderer", 0x1F01), ("version", 0x1F02))}
            require_hardware(info)
            info.update(api="EGLDevice/GLES2", cuda_device=self.cuda_device,
                        egl_version=f"{major.value}.{minor.value}", hardware=True)
            shaders.append(self._shader(0x8B31, b"""
                attribute vec3 position; attribute vec3 color;
                varying lowp vec3 faceColor;
                void main(){ gl_Position=vec4(position,1.0); faceColor=color; }
            """))
            shaders.append(self._shader(0x8B30, b"""
                precision mediump float; varying lowp vec3 faceColor;
                void main(){ gl_FragColor=vec4(faceColor,1.0); }
            """))
            program = gl.glCreateProgram()
            for shader in shaders:
                gl.glAttachShader(program, shader)
            gl.glBindAttribLocation(program, 0, b"position")
            gl.glBindAttribLocation(program, 1, b"color")
            gl.glLinkProgram(program)
            linked = C.c_int()
            gl.glGetProgramiv(program, 0x8B82, C.byref(linked))
            if not linked.value:
                log = C.create_string_buffer(4096)
                gl.glGetProgramInfoLog(program, len(log), None, log)
                raise RenderError(f"fixed shader link failed: {log.value.decode('utf8', 'replace')}")
            packed = []
            for tri, rgb in zip(mesh.triangles, mesh.colors):
                for index in tri:
                    packed.extend((*mesh.vertices[index], *(x / 255 for x in rgb)))
            data = (C.c_float * len(packed))(*packed)
            gl.glGenBuffers(1, C.byref(buffer))
            gl.glBindBuffer(0x8892, buffer.value)
            gl.glBufferData(0x8892, C.sizeof(data), data, 0x88E4)
            gl.glUseProgram(program)
            for index, offset in ((0, 0), (1, 12)):
                gl.glEnableVertexAttribArray(index)
                gl.glVertexAttribPointer(index, 3, 0x1406, 0, 24, C.c_void_p(offset))
            gl.glViewport(0, 0, mesh.width, mesh.height)
            gl.glDisable(0x0BD0)  # Dither changes byte-level reproducibility.
            gl.glDisable(0x0BE2)
            gl.glDisable(0x0B44)
            gl.glEnable(0x0B71)
            gl.glDepthFunc(0x0201)
            gl.glClearColor(*(x / 255 for x in BACKGROUND))
            gl.glClearDepthf(1.0)
            gl.glClear(0x4000 | 0x0100)
            gl.glDrawArrays(0x0004, 0, len(mesh.triangles) * 3)
            gl.glFinish()
            raw = (C.c_ubyte * (mesh.width * mesh.height * 4))()
            gl.glPixelStorei(0x0D05, 1)
            gl.glReadPixels(0, 0, mesh.width, mesh.height, 0x1908, 0x1401, raw)
            error = gl.glGetError()
            if error:
                raise RenderError(f"GLES draw/readback failed GL=0x{error:04x}")
            stride = mesh.width * 4
            pixels = b"".join(bytes(raw[row * stride:(row + 1) * stride])
                              for row in range(mesh.height - 1, -1, -1))
            return pixels, info
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if current:
                if buffer.value:
                    gl.glDeleteBuffers(1, C.byref(buffer))
                if program:
                    gl.glDeleteProgram(program)
                for shader in shaders:
                    gl.glDeleteShader(shader)
            failures = []
            if initialized:
                if not egl.eglMakeCurrent(display, None, None, None):
                    failures.append("eglMakeCurrent(detach)")
                if context and not egl.eglDestroyContext(display, context):
                    failures.append("eglDestroyContext")
                if surface and not egl.eglDestroySurface(display, surface):
                    failures.append("eglDestroySurface")
                if not egl.eglTerminate(display):
                    failures.append("eglTerminate")
            if failures:
                message = "EGL cleanup failed: " + ", ".join(failures)
                if primary is not None:
                    LOG.error("%s; primary=%s", message, primary)
                else:
                    raise RenderError(message)


def png_bytes(width, height, pixels):
    if len(pixels) != width * height * 4:
        raise RenderError("RGBA readback byte count mismatch")

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    stride = width * 4
    scanlines = b"".join(b"\0" + pixels[i:i + stride] for i in range(0, len(pixels), stride))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


class RenderService:
    def __init__(self, *, enable_gpu=False, cuda_device=0):
        self.enable_gpu = enable_gpu
        self.cuda_device = cuda_device
        self._lock = threading.Lock()

    def render(self, payload):
        started = time.perf_counter()
        mesh, requested, fallback_allowed = validate_request(payload)
        if not self._lock.acquire(blocking=False):
            raise RenderBusy("one request already active; no unbounded render queue")
        try:
            fallback_reason = None
            if requested == "gpu":
                try:
                    if not self.enable_gpu:
                        raise GPUUnavailable("GPU disabled by service configuration")
                    pixels, info = EGLRenderer(self.cuda_device).render(mesh)
                    backend = "gpu"
                except GPUUnavailable as exc:
                    if not fallback_allowed:
                        raise
                    fallback_reason = str(exc)
                    LOG.warning("CPU_FALLBACK requested=gpu reason=%s", fallback_reason)
                    pixels, info, backend = cpu_render(mesh), {
                        "hardware": False, "renderer": "python-barycentric-cpu"}, "cpu"
            else:
                pixels, info, backend = cpu_render(mesh), {
                    "hardware": False, "renderer": "python-barycentric-cpu"}, "cpu"
            png = png_bytes(mesh.width, mesh.height, pixels)
            return {"status": "CPU_FALLBACK" if fallback_reason else "OK",
                    "requested_backend": requested, "backend": backend, "renderer": info,
                    "fallback_reason": fallback_reason, "width": mesh.width, "height": mesh.height,
                    "triangles": len(mesh.triangles), "pixel_work": mesh.pixel_work,
                    "geometry_sha256": mesh.geometry_sha256,
                    "frame_sha256": hashlib.sha256(pixels).hexdigest(),
                    "png_sha256": hashlib.sha256(png).hexdigest(),
                    "image_png_base64": base64.b64encode(png).decode("ascii"),
                    "elapsed_s": time.perf_counter() - started}
        finally:
            self._lock.release()


def decode_request(data):
    if len(data) > MAX_BODY:
        raise InvalidInput("request byte budget exceeded")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidInput("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise InvalidInput(f"nonfinite JSON constant: {value}")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidInput(f"invalid JSON: {type(exc).__name__}") from exc


def make_http_server(service, port=0):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self):
            self.request.settimeout(3)
            super().setup()

        def log_message(self, fmt, *args):
            LOG.info("HTTP %s", fmt % args)

        def reply(self, status, value):
            body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/render":
                self.reply(404, {"error": "not_found"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                    raise InvalidInput("one decimal Content-Length required")
                if len(lengths[0]) > 10:
                    raise InvalidInput("Content-Length exceeds byte budget")
                length = int(lengths[0])
                if not 1 <= length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                    raise InvalidInput("invalid body size or transfer encoding")
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise InvalidInput("application/json required")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise InvalidInput("incomplete body")
                self.reply(200, service.render(decode_request(data)))
            except RenderError as exc:
                LOG.error("%s: %s", exc.code, exc)
                self.reply(exc.http_status, {"error": exc.code, "detail": str(exc)})
            except socket.timeout as exc:
                LOG.error("request_read_timeout: %s", exc)
                self.reply(408, {"error": "request_read_timeout"})
            except Exception as exc:
                LOG.exception("unhandled_render_failure")
                self.reply(500, {"error": "unhandled_render_failure", "type": type(exc).__name__})

        def do_GET(self):
            if self.path != "/health":
                self.reply(404, {"error": "not_found"})
                return
            self.reply(200, {"component": "offscreen-renderer", "gpu_enabled": service.enable_gpu,
                             "gpu_availability": "NOT_PROBED", "production_ready": False})

    class BoundedServer(HTTPServer):
        request_queue_size = 4

    return BoundedServer(("127.0.0.1", _integer(port, 0, 65535, "port")), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--enable-gpu", action="store_true")
    parser.add_argument("--cuda-device", type=int, default=0)
    parser.add_argument("--log-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("renderer must run without root privileges")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    log = args.log_dir / f"render-{time.time_ns()}-{os.getpid()}.log"
    handler = logging.FileHandler(log, mode="x")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
    server = make_http_server(RenderService(enable_gpu=args.enable_gpu,
                                            cuda_device=args.cuda_device), args.port)
    LOG.info("RENDER_READY host=127.0.0.1 port=%s uid=%s gpu_enabled=%s log=%s",
             server.server_port, os.geteuid(), args.enable_gpu, log)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
