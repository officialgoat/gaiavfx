"""Composite RGB patches into native samples, leaving uncovered samples untouched."""

import re

import numpy as np

from gaiavfx.shared.errors import ApplicationError


class NativePixels:
    def __init__(self, stream):
        self.width, self.height = stream["width"], stream["height"]
        self.format = stream["pix_fmt"]
        self.stream = stream
        self.packed = self.format in {"rgb24", "bgr24", "rgba", "bgra", "bgr0", "rgb0"}
        if self.packed:
            self.channels = 3 if self.format.endswith("24") else 4
            self.size = self.width * self.height * self.channels
            return
        match = re.fullmatch(r"yuv(j?)(420|422|444)p(?:(10|12|16)le)?", self.format)
        if not match:
            raise ApplicationError(f"Unsupported native conform pixel format: {self.format}", 422)
        self.bits = int(match[3] or 8)
        self.dtype = np.dtype("u1" if self.bits == 8 else "<u2")
        self.sx, self.sy = {"420": (2, 2), "422": (2, 1), "444": (1, 1)}[match[2]]
        self.cw = (self.width + self.sx - 1) // self.sx
        self.ch = (self.height + self.sy - 1) // self.sy
        self.size = (self.width * self.height + 2 * self.cw * self.ch) * self.dtype.itemsize
        self.full = bool(match[1]) or stream.get("color_range") == "pc"

    def paste(self, data, rgba, rect):
        x0, y0, x1, y1 = rect
        alpha = rgba[:, :, 3].astype(np.float64) / 255
        if not alpha.any():
            return data
        if self.packed:
            frame = (
                np.frombuffer(data, np.uint8).copy().reshape(self.height, self.width, self.channels)
            )
            rgb = rgba[:, :, :3]
            if self.format.startswith("bgr"):
                rgb = rgb[:, :, ::-1]
            dest = frame[y0:y1, x0:x1, :3]
            dest[:] = np.rint(dest * (1 - alpha[:, :, None]) + rgb * alpha[:, :, None])
            return frame.tobytes()
        # Same matrix/range defaults as replacement_steps.decode_rgb. Do not
        # round-trip the source through RGB: even alpha-zero samples must be exact.
        kr, kb = {
            "bt470bg": (0.299, 0.114),
            "smpte170m": (0.299, 0.114),
            "bt2020nc": (0.2627, 0.0593),
        }.get(self.stream.get("color_space"), (0.2126, 0.0722))
        rgb = rgba[:, :, :3].astype(np.float64) / 255
        r, g, b = (rgb[:, :, i] for i in range(3))
        y = kr * r + (1 - kr - kb) * g + kb * b
        u, v = (b - y) / (2 * (1 - kb)), (r - y) / (2 * (1 - kr))
        scale, maximum = 2 ** (self.bits - 8), 2**self.bits - 1
        values = (
            (y * maximum, u * maximum + 128 * scale, v * maximum + 128 * scale)
            if self.full
            else ((16 + 219 * y) * scale, (128 + 224 * u) * scale, (128 + 224 * v) * scale)
        )
        flat = np.frombuffer(data, self.dtype).copy()
        offset = 0
        for i, value in enumerate(values):
            w, h = (self.width, self.height) if i == 0 else (self.cw, self.ch)
            plane = flat[offset : offset + w * h].reshape(h, w)
            offset += w * h
            if i == 0 or (self.sx, self.sy) == (1, 1):
                dest = plane[y0:y1, x0:x1]
                dest[:] = np.clip(np.rint(dest * (1 - alpha) + value * alpha), 0, maximum)
                continue
            # Chroma is shared by a subsampling cell. Only update cells wholly
            # inside the rectangle, so an odd rectangle edge cannot alter an
            # outside native sample. Partial alpha is area-weighted within cells.
            cx0, cy0 = (x0 + self.sx - 1) // self.sx, (y0 + self.sy - 1) // self.sy
            cx1, cy1 = x1 // self.sx, y1 // self.sy
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            ys, xs = (
                slice(cy0 * self.sy - y0, cy1 * self.sy - y0),
                slice(cx0 * self.sx - x0, cx1 * self.sx - x0),
            )
            shape = (cy1 - cy0, self.sy, cx1 - cx0, self.sx)
            a = alpha[ys, xs].reshape(shape).mean(axis=(1, 3))
            premultiplied = (value[ys, xs] * alpha[ys, xs]).reshape(shape).mean(axis=(1, 3))
            dest = plane[cy0:cy1, cx0:cx1]
            dest[:] = np.clip(np.rint(dest * (1 - a) + premultiplied), 0, maximum)
        return flat.tobytes()
