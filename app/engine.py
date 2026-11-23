from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

if __package__:
    from .bokeh_sources import reconstruct_bokeh_sources
else:
    from bokeh_sources import reconstruct_bokeh_sources

# Depth modes, kept in sync with the combo box order in main.py.
MODE_AI = 0
MODE_CENTER = 1
MODE_MANUAL = 2


class RenderCancelled(Exception):
    """A preview was superseded before it completed."""


def _check_cancelled(should_cancel):
    if should_cancel is not None and should_cancel():
        raise RenderCancelled()


def _fast_blur(image, sigma):
    """Gaussian-like blur that stays cheap for large defocus radii.

    Three box passes approximate a Gaussian closely enough for out-of-focus
    backgrounds, and cost O(1) per pixel regardless of the kernel size.
    """
    if sigma < 2.5:
        return cv2.GaussianBlur(image, (0, 0), sigmaX=sigma, sigmaY=sigma)
    size = int((12.0 * sigma * sigma / 3.0 + 1.0) ** 0.5)
    if size % 2 == 0:
        size += 1
    size = max(3, size)
    blurred = cv2.blur(image, (size, size), borderType=cv2.BORDER_REFLECT)
    blurred = cv2.blur(blurred, (size, size), borderType=cv2.BORDER_REFLECT)
    return cv2.blur(blurred, (size, size), borderType=cv2.BORDER_REFLECT)


@dataclass
class EngineSettings:
    blur_strength: float = 0.55
    bokeh_strength: float = 0.35
    aperture: float = 1.4
    focus: float = 0.45
    depth_strength: float = 0.70
    protect_subject: bool = True
    bokeh_blades: int = 7
    bokeh_density: float = 0.55
    bokeh_size: float = 1.0
    bokeh_aa: int = 1
    full_luminance_bokeh: bool = False
    spherical: float = 0.0
    cat_eye: float = 0.0
    subject_expand: float = 0.0
    subject_feather: float = 0.0
    # 高光溢出补偿：越亮的光源，其光斑叠加越透明，避免过亮处糊成纯白。
    highlight_rolloff: bool = False
    highlight_threshold: float = 0.80


class BokehEngine:
    """
    CPU-first, GPU-aware image engine.

    The depth-aware blur is a practical approximation, not a physically exact
    lens renderer. Center focus uses a one-sided radial falloff ("sharp inside
    the focus radius"), while AI/manual focus uses a focus plane in the
    estimated depth map.
    """

    def __init__(self):
        self.backend_description = self._detect_backend()

    def _detect_backend(self):
        try:
            if hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0:
                # Detection only: all current operators below are CPU code.
                return "CUDA 设备可用（当前算子为 CPU 实现）"
        except Exception:
            pass
        return "CPU 后端（兼容模式）"

    def _center_depth(self, h, w):
        """Radial distance from the subject point, normalized to roughly 0..1.

        0 = center, 1 = farthest corner. This is the no-model fallback.
        """
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = w * 0.5, h * 0.46
        dist = np.sqrt(((xx - cx) / max(w, 1)) ** 2 + ((yy - cy) / max(h, 1)) ** 2)
        return np.clip(dist / max(float(dist.max()), 1e-6), 0, 1)

    def center_depth(self, h, w):
        """Public accessor used by manual focus sampling."""
        return self._center_depth(h, w)

    def _make_coc(self, depth, settings, one_sided):
        if one_sided:
            # Center mode: everything inside the focus radius stays sharp and
            # the blur ramps up towards the corners. This matches the label
            # "焦点深度" much better than a sharp ring at abs(depth - focus).
            focus_limit = 1.0 if settings.full_luminance_bokeh else 0.98
            focus = float(np.clip(settings.focus, 0.0, focus_limit))
            coc = np.clip((depth - focus) / max(1.0 - focus, 1e-3), 0, 1)
            coc = np.clip(coc * (0.45 + settings.depth_strength * 1.35), 0, 1)
        else:
            # AI/manual mode: a real focus plane, blur on both sides of it.
            coc = np.abs(depth - settings.focus)
            coc = np.clip(coc * (1.0 + settings.depth_strength * 1.6), 0, 1)
        aperture_floor = 0.95 if settings.full_luminance_bokeh else 1.2
        gain_floor = 0.30 if settings.full_luminance_bokeh else 0.35
        aperture_gain = np.clip((8.0 / max(settings.aperture, aperture_floor)) ** 0.42 / 2.3, gain_floor, 2.5)
        # Above the nominal range, increase kernel size without clipping more
        # pixels to the same depth or discarding extra in-focus pixels.
        blur = min(settings.blur_strength, 1.0) if settings.full_luminance_bokeh else settings.blur_strength
        return np.clip(coc * blur * aperture_gain, 0, 1)

    @staticmethod
    def _aperture_points(cx, cy, rx, ry, blade_count, rotation):
        """Rounded aperture outline; real blades are curved, not sharp."""
        if blade_count < 3:
            # Dense sampling: a coarse polygon reads as facets once the disc is
            # drawn large (the "circle" aperture must stay a true circle).
            angles = np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False)
            return np.stack(
                [cx + rx * np.cos(angles), cy + ry * np.sin(angles)], axis=1
            )

        angles = rotation + np.arange(blade_count) * (2.0 * np.pi / blade_count)
        points = np.stack(
            [cx + rx * np.cos(angles), cy + ry * np.sin(angles)], axis=1
        )
        # Small corner cut: real aperture blades are curved at the tips, but the
        # flat sides of a stopped-down aperture should stay visible.
        cut = 0.12
        rounded = []
        for index in range(len(points)):
            p0 = points[index]
            p1 = points[(index + 1) % len(points)]
            rounded.append(p0 * (1.0 - cut) + p1 * cut)
            rounded.append(p0 * cut + p1 * (1.0 - cut))
        return np.asarray(rounded)

    def _bokeh_overlay(self, blurred, source, coc, settings, sharp=None, should_cancel=None):
        """Render distinct aperture-shaped bokeh discs on top of the blur.

        The old implementation dilated highlights and Gaussian-blurred them,
        which produced a soft haze. Real defocused point lights keep a visible
        shape (aperture polygon), a brighter rim and often a cat's-eye squash
        near the frame edges, so this version draws the discs explicitly.
        """
        bokeh = settings.bokeh_strength
        if bokeh <= 0.001:
            return blurred

        h, w = source.shape[:2]
        # Render the bokeh layer at full resolution (with a sane cap for huge
        # prints). A preview-sized layer upscaled to 24 MP is what produced the
        # stair-step/jagged disc edges in exported files.
        max_side = max(h, w)
        scale = 1.0 if max_side <= 5000 else 5000.0 / max_side
        if scale < 1.0:
            sw, sh = max(1, int(w * scale)), max(1, int(h * scale))
            small = cv2.resize(source, (sw, sh), interpolation=cv2.INTER_AREA)
            coc_s = cv2.resize(coc, (sw, sh), interpolation=cv2.INTER_AREA)
        else:
            sw, sh = w, h
            small, coc_s = source, coc

        f = small.astype(np.float32) / 255.0
        lum = 0.2126 * f[:, :, 0] + 0.7152 * f[:, :, 1] + 0.0722 * f[:, :, 2]

        full_range = settings.full_luminance_bokeh
        threshold = 0.72
        mask = np.sqrt(np.clip(lum, 0, 1)) if full_range else np.clip((lum - threshold) / max(1.0 - threshold, 1e-6), 0, 1)
        mask *= np.clip(coc_s * 1.4, 0, 1)
        if float(mask.max()) < 0.02:
            return blurred
        density = float(np.clip(settings.bokeh_density, 0.0, 1.0))
        radius_scale = max(1.0, max(sw, sh) / 1000.0)

        # Low-frequency ambient/background colour with highlights (and the sharp
        # subject) excluded. White lights pick up this tint instead of staying
        # pure white, which is what real bokeh does in a coloured scene.
        bg_side = 320
        bg_factor = min(1.0, bg_side / max(sw, sh))
        bw = max(1, int(sw * bg_factor))
        bh = max(1, int(sh * bg_factor))
        bg_small = cv2.resize(f, (bw, bh), interpolation=cv2.INTER_AREA)
        bg_lum = 0.2126 * bg_small[:, :, 0] + 0.7152 * bg_small[:, :, 1] + 0.0722 * bg_small[:, :, 2]
        bg_weight = 1.0 - np.clip((bg_lum - 0.72) / 0.28, 0, 1)
        if sharp is not None:
            fg_small = cv2.resize(sharp, (bw, bh), interpolation=cv2.INTER_AREA)
            bg_weight *= np.clip(1.0 - fg_small, 0.0, 1.0)
        bg_color_map = self._masked_blur(bg_small, bg_weight, 8.0)

        # Find local highlight peaks and reject flat bright areas (sky, walls):
        # only locally prominent points become visible bokeh discs. The NMS
        # radius scales with resolution so one upscaled light does not produce
        # several additive discs (which previously made exports too bright).
        kernel_size = max(5, int(round((13 if full_range else 7) * radius_scale)))
        if kernel_size % 2 == 0:
            kernel_size += 1
        kernel = np.ones((kernel_size, kernel_size), np.float32)
        local_max = cv2.dilate(lum, kernel)
        local_mean = cv2.GaussianBlur(lum, (0, 0), sigmaX=4.2 * radius_scale)
        prominence = np.clip((lum - local_mean) * 4.0, 0, 1)
        peak_mask = (
            (lum >= local_max - 1e-4) & (mask > 0.02) & (prominence > 0.03)
        ).astype(np.uint8)

        count, _labels, _stats, centroids = cv2.connectedComponentsWithStats(peak_mask, 8)
        peaks = []
        for index in range(1, count):
            cx, cy = centroids[index]
            xi = min(sw - 1, max(0, int(round(cx))))
            yi = min(sh - 1, max(0, int(round(cy))))
            weight = float(mask[yi, xi] * max(prominence[yi, xi], 0.15))
            if weight > (0.001 if full_range else 0.006 + 0.04 * (1.0 - density)):
                peaks.append((weight, float(cx), float(cy)))
        if not peaks:
            return blurred

        peaks.sort(key=lambda item: item[0], reverse=True)
        peaks = peaks[: int(80 + 640 * density)]

        blade_count = int(settings.bokeh_blades)
        aa = max(1, min(3, int(getattr(settings, "bokeh_aa", 1))))
        # Spherical aberration reshapes the highlight disc itself, so it is read
        # once and applied to every drawn disc (see the per-disc block below).
        spherical = float(np.clip(getattr(settings, "spherical", 0.0), -1.0, 1.0))
        # Cat's-eye strength (mechanical vignetting at the frame edge).
        cat_eye = float(np.clip(getattr(settings, "cat_eye", 0.0), 0.0, 1.0))
        # Highlight overflow compensation (read once, applied per disc below).
        rolloff = bool(getattr(settings, "highlight_rolloff", False))
        rolloff_at = float(
            np.clip(getattr(settings, "highlight_threshold", 0.80), 0.0, 1.0)
        )
        # Radii were originally tuned on a 1000px working image. Scale them with
        # the render resolution so a 24 MP export has the same relative bokeh
        # size as the preview instead of tiny discs.
        base_radius = (2.5 + 26.0 * max(bokeh, 0.12)) * radius_scale * max(settings.bokeh_size, 0.05)
        if full_range:
            base_radius *= 0.5 + settings.blur_strength
        aperture_factor = (1.4 / max(settings.aperture, 1.2)) ** 0.45
        layer = np.zeros_like(f)

        for index, (weight, cx, cy) in enumerate(peaks):
            if index % 16 == 0:
                _check_cancelled(should_cancel)
            xi = min(sw - 1, max(0, int(round(cx))))
            yi = min(sh - 1, max(0, int(round(cy))))
            defocus = float(np.clip(coc_s[yi, xi] * 1.3, 0.0, 1.0))
            if defocus < 0.08:
                continue
            radius = base_radius * aperture_factor * (0.25 + 1.05 * defocus)
            if radius < 1.2 * radius_scale:
                continue
            # Small deterministic size variation makes the field look less
            # computer-generated without changing the average CoC.
            jitter = 0.90 + 0.20 * ((np.sin((index + 1) * 12.9898) * 43758.5453) % 1.0)
            radius = min(radius * jitter, 40.0 * radius_scale * max(settings.bokeh_size, 1.0))

            # Cat's-eye: real mechanical vignetting squeezes discs near the
            # frame edge horizontally. It is 0 by default, so the "circle"
            # aperture stays a true circle everywhere and the user can dial the
            # squash in when they want the look.
            edge = abs(cx - sw * 0.5) / max(sw * 0.5, 1.0)
            squash = 1.0 - (0.5 * cat_eye) * edge * edge
            if blade_count < 3:
                rotation = 0.0
            else:
                rotation = (index * 0.61803398875) % (2.0 * np.pi / blade_count)
            points = self._aperture_points(
                cx, cy, radius * squash, radius, blade_count, rotation
            )

            source_color = f[yi, xi]
            bx = min(bw - 1, max(0, int(cx * bw / sw)))
            by = min(bh - 1, max(0, int(cy * bh / sh)))
            bg_color = bg_color_map[by, bx]
            bg_peak = float(bg_color.max())
            if bg_peak > 0.08 and not full_range:
                # Normalise to preserve hue but not darken the bokeh: brightness
                # still comes from the highlight, colour comes from the scene.
                bg_norm = np.clip(bg_color / bg_peak, 0.0, 1.5)
                saturation = (
                    float(source_color.max()) - float(source_color.min())
                ) / max(float(source_color.max()), 1e-4)
                bg_mix = 0.25 + 0.35 * (1.0 - saturation)
                color = source_color * (1.0 - bg_mix) + bg_norm * bg_mix
            else:
                color = source_color
            color = np.clip(color if full_range else color * 0.85 + 0.15, 0.0, 1.0)

            strength = float(np.clip(weight * (0.35 + 0.8 * max(bokeh, 0.2)), 0.0, 1.0))
            if full_range:
                # Visible aperture contours for already-blurred highlights;
                # intensity remains tied continuously to their source light.
                strength = float(np.clip(weight * (2.0 + 2.0 * bokeh), 0.0, 0.55))
            strength *= 1.0 - 0.20 * edge * edge  # mild optical vignetting
            if rolloff:
                # Highlight overflow compensation: sources brighter than the
                # threshold fade their disc out, so a blown-out light never
                # stacks into a flat pure-white blob.
                peak_luminance = float(lum[yi, xi])
                if peak_luminance > rolloff_at:
                    over = (peak_luminance - rolloff_at) / max(1.0 - rolloff_at, 1e-3)
                    strength *= 1.0 - (0.45 if full_range else 0.85) * min(1.0, over)

            # Draw into a small local patch and ADD it to the layer. Using
            # fillPoly directly on the shared layer would overwrite earlier
            # discs; real bokeh is additive light, so overlaps brighten and mix.
            x0 = max(0, int(np.floor(points[:, 0].min())) - 1)
            x1 = min(sw, int(np.ceil(points[:, 0].max())) + 2)
            y0 = max(0, int(np.floor(points[:, 1].min())) - 1)
            y1 = min(sh, int(np.ceil(points[:, 1].max())) + 2)
            if x1 <= x0 or y1 <= y0:
                continue
            patch_h = y1 - y0
            patch_w = x1 - x0
            local = np.round(
                points - np.array([x0, y0], dtype=np.float32)
            ).astype(np.int32)
            # Spherical aberration decides whether the disc's light sits on the
            # rim or in the core. Under-corrected lenses brighten and thicken the
            # rim (bright-edge "soap bubble" bokeh); over-corrected lenses dim the
            # rim and build a bright core; a corrected lens stays near-uniform.
            if spherical >= 0.0:
                fill_gain = 1.0 - 0.28 * spherical
                rim_gain = 1.0 + 0.30 * spherical
                core_gain = 0.0
                rim_thickness = max(
                    1, int(round(radius * (0.06 + 0.05 * spherical)))
                )
            else:
                m = -spherical
                fill_gain = 1.0 - 0.35 * m
                rim_gain = 1.0 - 0.65 * m
                core_gain = 0.85 * m
                rim_thickness = max(1, int(round(radius * 0.05)))

            fill_color = (color * (strength * 0.95 * fill_gain)).tolist()
            rim_color = (color * (strength * 0.99 * rim_gain)).tolist()

            core_local = None
            core_color = None
            if core_gain > 0.01:
                core = (points - np.array([cx, cy], dtype=np.float32)) * 0.55
                core += np.array([cx, cy], dtype=np.float32)
                core_local = np.round(
                    core - np.array([x0, y0], dtype=np.float32)
                ).astype(np.int32)
                core_color = (
                    color * min(1.0, strength * (0.95 * fill_gain + core_gain))
                ).tolist()

            ring_local = None
            if spherical > 0.0 and radius >= 16.0 and index % 5 == 0:
                ring = (points - np.array([cx, cy], dtype=np.float32)) * 0.68
                ring += np.array([cx, cy], dtype=np.float32)
                ring_local = np.round(
                    ring - np.array([x0, y0], dtype=np.float32)
                ).astype(np.int32)

            if aa > 1:
                # Per-disc supersampling: draw the polygon at 2x/3x and downsample.
                # This anti-aliases the fill edge, not just the outline.
                local_aa = local * aa
                patch = np.zeros((patch_h * aa, patch_w * aa, 3), dtype=np.float32)
                cv2.fillPoly(patch, [local_aa], fill_color, cv2.LINE_8)
                cv2.polylines(
                    patch, [local_aa], True, rim_color,
                    max(1, rim_thickness * aa), cv2.LINE_8,
                )
                if core_local is not None:
                    cv2.fillPoly(patch, [core_local * aa], core_color, cv2.LINE_8)
                if ring_local is not None:
                    ring_mask = np.zeros((patch_h * aa, patch_w * aa), dtype=np.float32)
                    cv2.polylines(
                        ring_mask, [ring_local * aa], True, 1.0,
                        max(1, aa), cv2.LINE_8,
                    )
                    patch += ring_mask[..., None] * (color * (strength * 0.05))
                patch = cv2.resize(
                    patch, (patch_w, patch_h), interpolation=cv2.INTER_AREA
                )
            else:
                patch = np.zeros((patch_h, patch_w, 3), dtype=np.float32)
                # Aperture-shaped fill, then the rim/core that spherical
                # aberration emphasised.
                cv2.fillPoly(patch, [local], fill_color, cv2.LINE_AA)
                cv2.polylines(
                    patch, [local], True, rim_color, rim_thickness, cv2.LINE_AA
                )
                if core_local is not None:
                    cv2.fillPoly(patch, [core_local], core_color, cv2.LINE_AA)
                if ring_local is not None:
                    ring_mask = np.zeros((patch_h, patch_w), dtype=np.float32)
                    cv2.polylines(
                        ring_mask, [ring_local], True, 1.0, 1, cv2.LINE_AA
                    )
                    patch += ring_mask[..., None] * (color * (strength * 0.05))
            layer[y0:y1, x0:x1] += patch

        # Clip additive overlaps before 8-bit conversion.
        layer = np.clip(layer, 0.0, 1.0)
        layer_u8 = (layer * 255.0).astype(np.uint8)
        if scale < 1.0:
            layer_u8 = cv2.resize(layer_u8, (w, h), interpolation=cv2.INTER_LINEAR)
        # Halo in 8-bit space: avoids another full-resolution float buffer and
        # softens the polygon edges so exports do not show stair-step jaggies.
        # Sigma scales with the working resolution for consistent softness.
        halo_sigma = max(0.8, 2.0 * radius_scale)
        halo = cv2.GaussianBlur(layer_u8, (0, 0), halo_sigma)
        layer_u8 = cv2.addWeighted(layer_u8, 1.0, halo, 0.35, 0.0)
        if sharp is not None:
            # Background bokeh must stay behind the sharp foreground: clip any
            # disc or halo that would otherwise paint over the subject.
            bg = np.clip(1.0 - sharp, 0.0, 1.0)
            bg_u8 = cv2.cvtColor((bg * 255.0).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            layer_u8 = cv2.multiply(layer_u8, bg_u8, scale=1.0 / 255.0)
        final_sigma = max(0.6, 0.6 * radius_scale * scale)
        layer_u8 = cv2.GaussianBlur(layer_u8, (0, 0), final_sigma)
        if full_range:
            # Screen blend is bounded, unlike additive compositing. The
            # full-image PSF below still carries light at every brightness.
            base = blurred.astype(np.float32) / 255.0
            light = layer_u8.astype(np.float32) / 255.0
            return np.clip((base + light * (1.0 - base)) * 255.0, 0, 255).astype(np.uint8)
        return cv2.add(blurred, layer_u8)

    @staticmethod
    def _spherical_profile(normalized_radius, spherical):
        """Radial energy weighting of a defocus PSF under spherical aberration.

        Only the shape matters: the caller renormalises the disc kernel, so the
        total light is unchanged. Spherical aberration redistributes energy
        inside the circle of confusion instead of adding any:

        * ``spherical > 0`` - under-corrected lens. Energy migrates to the rim,
          which is the bright-edged "soap bubble" bokeh of simple fast lenses.
        * ``spherical < 0`` - over-corrected lens. Energy collects at the centre
          for a soft, bright-core disc.
        * ``0`` - a well-corrected lens leaves the plain uniform disc.
        """
        s = float(np.clip(spherical, -1.0, 1.0))
        if abs(s) < 1e-3:
            return np.ones_like(normalized_radius)
        if s > 0.0:
            ring = np.exp(-((normalized_radius - 1.0) / 0.18) ** 2)
            return 1.0 + 2.4 * s * ring
        m = -s
        core = np.exp(-(normalized_radius / 0.45) ** 2)
        return (1.0 - 0.85 * m) + 1.7 * m * core

    @staticmethod
    def _disc_kernel(radius, blade_count, spherical=0.0):
        """Aperture-shaped (pillbox) defocus kernel, normalised to unit sum."""
        radius = max(1, int(round(radius)))
        size = radius * 2 + 1
        if blade_count < 3:
            # Analytic anti-aliased disc from the distance field. cv2.circle()
            # rasterises a coarse polygon whose flat edges are visible in the
            # defocused background (severe at small radii), so a true circle is
            # built here instead.
            yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
            dist = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2)
            kernel = np.clip(radius + 0.5 - dist, 0.0, 1.0).astype(np.float32)
        else:
            kernel = np.zeros((size, size), dtype=np.float32)
            points = BokehEngine._aperture_points(
                radius, radius, radius, radius, blade_count, 0.0
            )
            cv2.fillPoly(
                kernel, [np.round(points).astype(np.int32)], 1.0, cv2.LINE_AA
            )
        # Spherical aberration reshapes the radial energy of the disc. Applied
        # before the smoothing below, and the kernel is renormalised, so the
        # bright-rim / bright-core look is preserved with no change in exposure.
        if abs(float(np.clip(spherical, -1.0, 1.0))) > 1e-3:
            yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
            dist = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2)
            t = np.clip(dist / max(float(radius), 1e-6), 0.0, 1.5)
            kernel *= BokehEngine._spherical_profile(t, spherical)
        # A real defocus PSF has a slightly soft edge from diffraction/aberration;
        # this also suppresses FFT ringing on high-contrast background edges.
        kernel = cv2.GaussianBlur(kernel, (0, 0), 0.6)
        total = float(kernel.sum())
        return kernel / total if total > 0 else kernel

    @staticmethod
    @lru_cache(maxsize=160)
    def _wavefront_kernel(radius, blade_count, spherical, field_x=0.0, field_y=0.0, defocus_sign=1):
        """Approximate an incoherent PSF from a defocused complex pupil.

        Wavefront coefficients are illustrative, not a calibrated lens model.
        """
        n = 256
        yy, xx = np.mgrid[:n, :n].astype(np.float32)
        pupil_radius = 80
        x, y = (xx - n / 2) / pupil_radius, (yy - n / 2) / pupil_radius
        rho2 = x*x + y*y
        if blade_count < 3:
            pupil = (rho2 <= 1.0).astype(np.float32)
        else:
            pupil = np.zeros((n, n), np.float32)
            points = BokehEngine._aperture_points(n/2, n/2, pupil_radius, pupil_radius, blade_count, 0)
            cv2.fillPoly(pupil, [np.round(points).astype(np.int32)], 1.0)
        # A shifted barrel opening clips the off-axis pupil. Center the
        # transmitted footprint so the approximation doesn't shift the scene.
        pupil *= ((x - field_x)**2 + (y - field_y)**2 <= 1.0)
        waves = float(np.clip(radius / 2.5, 2.5, 13.0))
        aberration = np.clip(spherical, -2, 2) * rho2 * (1.0 - 0.5 * rho2)
        # Keep the lens aberration fixed while defocus crosses the focus plane.
        phase = 2.0 * np.pi * waves * (defocus_sign * rho2 + 0.45 * aberration)
        # The phase slope at the pupil rim sets the geometric footprint.
        # Include diffraction outside that radius before reducing to pixels.
        half = min(n // 2 - 1, int(np.ceil(2.0 * waves * n / pupil_radius)) + 10)
        center = n // 2
        psf = np.zeros((2 * half + 1, 2 * half + 1), np.float32)
        # Approximate a broadband source. Map each wavelength's diffraction
        # pattern to the same sensor coordinates before summing intensities.
        for wavelength, weight in ((0.85, 0.25), (1.0, 0.50), (1.17, 0.25)):
            angle = phase / wavelength
            field = cv2.merge((pupil * np.cos(angle), pupil * np.sin(angle)))
            # Shifting the pupil to the origin changes Fourier phase only,
            # so it can be omitted when computing just intensity.
            spectrum = cv2.dft(field)
            power = cv2.mulSpectrums(spectrum, spectrum, 0, conjB=True)
            intensity = np.fft.fftshift(power[..., 0])
            transform = cv2.getRotationMatrix2D((center, center), 0, wavelength)
            transform[:, 2] -= center - half
            band = cv2.warpAffine(intensity, transform, psf.shape[::-1], flags=cv2.INTER_LINEAR)
            psf += band * (weight / max(float(band.sum()), 1e-12))
        if field_x or field_y:
            mass = cv2.moments(psf)
            dx = half - mass["m10"] / mass["m00"]
            dy = half - mass["m01"] / mass["m00"]
            psf = cv2.warpAffine(psf, np.float32([[1, 0, dx], [0, 1, dy]]), psf.shape[::-1])
        psf = cv2.resize(psf, (2*radius+1, 2*radius+1), interpolation=cv2.INTER_AREA)
        return psf / max(float(psf.sum()), 1e-12)

    def _render_wavefront(self, linear, signed_coc, background, radius, settings, should_cancel=None):
        """Spread radiance over sampled depth/field PSFs in linear light."""
        coc = np.abs(signed_coc)
        radiance = reconstruct_bokeh_sources(linear, coc, background, radius, settings)
        _check_cancelled(should_cancel)
        h, w = coc.shape
        steps = 6
        depth = signed_coc * steps
        sharp_weight = np.maximum(1.0 - np.abs(depth), 0.0)
        far = np.empty((h, w, 4), np.float32)
        far[..., :3] = linear * sharp_weight[..., None]
        far[..., 3] = sharp_weight
        near = np.zeros_like(far) if np.any(depth > 0) else None

        cat_eye = float(np.clip(settings.cat_eye, 0.0, 1.6))
        if cat_eye > 0:
            x = np.linspace(-1, 1, w, dtype=np.float32) if w > 1 else np.zeros(1, np.float32)
            y = np.linspace(-1, 1, h, dtype=np.float32) if h > 1 else np.zeros(1, np.float32)
            fields = [
                (fx, fy, np.maximum(1 - abs(x - fx), 0), np.maximum(1 - abs(y - fy), 0))
                for fy in (-1, 0, 1) for fx in (-1, 0, 1)
            ]
        else:
            fields = [(0, 0, np.ones(w, np.float32), np.ones(h, np.float32))]

        # Record each field's nonzero support once instead of scanning it for
        # every radius. A layer can occupy only a small part of that field.
        regions = []
        for fx, fy, wx, wy in fields:
            xs, ys = np.flatnonzero(wx), np.flatnonzero(wy)
            if xs.size and ys.size:
                regions.append((fx, fy, wx, wy, xs[0], xs[-1] + 1, ys[0], ys[-1] + 1))
        source = np.empty_like(far)
        for sign, output in ((-1, far), (1, near)):
            if output is None:
                continue
            for i in range(1, steps + 1):
                depth_weight = np.maximum(1.0 - np.abs(depth - sign * i), 0.0)
                if sign > 0:
                    depth_weight *= background
                dx, dy, dw, dh = cv2.boundingRect((depth_weight > 0).astype(np.uint8))
                if not dw:
                    continue
                r = max(1, int(round(radius * i / steps)))
                for fx, fy, wx, wy, left, right, top, bottom in regions:
                    _check_cancelled(should_cancel)
                    x0, x1 = max(dx, left), min(dx + dw, right)
                    y0, y1 = max(dy, top), min(dy + dh, bottom)
                    if x0 >= x1 or y0 >= y1:
                        continue
                    # Include the kernel's complete reach, so cropping removes
                    # only work on zeros, with the same true image borders.
                    tx, ty, tw, th = cv2.boundingRect(
                        (depth_weight[y0:y1, x0:x1] > 0).astype(np.uint8)
                    )
                    if not tw:
                        continue
                    x0, y0 = x0 + tx, y0 + ty
                    sx = slice(max(0, x0 - r), min(w, x0 + tw + r))
                    sy = slice(max(0, y0 - r), min(h, y0 + th + r))
                    patch = source[:sy.stop - sy.start, :sx.stop - sx.start]
                    weight = patch[..., 3]
                    np.multiply(depth_weight[sy, sx], wy[sy, None], out=weight)
                    weight *= wx[None, sx]
                    np.multiply(radiance[sy, sx], weight[..., None], out=patch[..., :3])
                    kernel = self._wavefront_kernel(
                        r, settings.bokeh_blades, settings.spherical, fx * cat_eye * 0.55, fy * cat_eye * 0.55,
                        defocus_sign=-sign,
                    )
                    output[sy, sx] += cv2.filter2D(
                        patch, -1, kernel[::-1, ::-1], borderType=cv2.BORDER_REPLICATE
                    )

        # Normalized convolution keeps a flat background stable across changes
        # in depth and field. Within each layer light contributions add.
        weight = far[..., 3:]
        far_color = np.where(weight > 1e-6, far[..., :3] / np.maximum(weight, 1e-6), linear)
        if near is None:
            return far_color, None, None
        weight = near[..., 3]
        near_color = near[..., :3] / np.maximum(weight[..., None], 1e-6)
        return far_color, near_color, np.clip(weight, 0.0, 1.0)

    @staticmethod
    def _encode_srgb(linear):
        linear = np.clip(linear, 0.0, 1.0)
        return np.where(linear <= 0.0031308, 12.92 * linear,
                        1.055 * linear ** (1.0 / 2.4) - 0.055) * 255.0

    def _composite_wavefront(
        self, image, sharp, far, near, near_alpha, protect_subject, return_layers, should_cancel=None,
    ):
        """Composite far, focal and near layers and keep the export mask consistent."""
        h, w = sharp.shape
        needs_resize = far.shape[:2] != (h, w)
        small_alpha = near_alpha
        if needs_resize and near is not None:
            near_alpha = cv2.resize(small_alpha, (w, h), interpolation=cv2.INTER_LINEAR)
        final = np.empty_like(image)
        effect = np.empty_like(image) if return_layers else None
        alpha = np.empty((h, w), np.uint8) if return_layers else None
        for c in range(3):
            far_plane = far[..., c]
            near_plane = near[..., c] if near is not None else None
            if needs_resize:
                far_plane = cv2.resize(far_plane, (w, h), interpolation=cv2.INTER_LINEAR)
                if near_plane is not None:
                    # Resize premultiplied color to avoid dark foreground edges.
                    near_plane = cv2.resize(near_plane * small_alpha, (w, h), interpolation=cv2.INTER_LINEAR)
            # Limit full-resolution float scratch space to one channel and
            # encode/blend only a strip at a time, including for layer export.
            for start in range(0, h, 256):
                _check_cancelled(should_cancel)
                rows = slice(start, min(h, start + 256))
                sharp_rows = sharp[rows]
                far_color = self._encode_srgb(far_plane[rows])
                if near_plane is None:
                    focal_alpha = sharp_rows
                    premul = far_color * (1.0 - focal_alpha)
                else:
                    coverage = near_alpha[rows]
                    near_rows = near_plane[rows]
                    if needs_resize:
                        near_rows = near_rows / np.maximum(coverage, 1e-6)
                    if protect_subject:
                        coverage = coverage * (1.0 - sharp_rows)
                    focal_alpha = sharp_rows * (1.0 - coverage)
                    premul = far_color * (1.0 - coverage) * (1.0 - sharp_rows)
                    premul += self._encode_srgb(near_rows) * coverage
                final[rows, :, c] = np.clip(np.rint(premul + image[rows, :, c] * focal_alpha), 0, 255)
                if effect is not None:
                    effect_alpha = 1.0 - focal_alpha
                    effect[rows, :, c] = np.clip(np.rint(premul / np.maximum(effect_alpha, 1e-6)), 0, 255)
                    if c == 0:
                        alpha[rows] = np.rint(effect_alpha * 255)
        if effect is not None:
            return final, effect, alpha
        return final

    @staticmethod
    def _disc_blur_levels(image, radii, blade_count, spherical=0.0, should_cancel=None):
        """Convolve the image with aperture-shaped PSFs at several radii.

        Uses a single padded FFT of the image, then one inverse FFT per radius,
        so the whole depth-varying background costs only a few transforms.
        """
        h, w = image.shape[:2]
        radii = [max(1, int(round(radius))) for radius in radii]
        max_radius = max(radii)

        # Replicate the border before the FFT; zero padding would darken the
        # image edges by one PSF radius, which is very visible in a defocus blur.
        pad = max_radius
        extended = cv2.copyMakeBorder(
            image, pad, pad, pad, pad, borderType=cv2.BORDER_REPLICATE
        )
        eh, ew = extended.shape[:2]
        fh = cv2.getOptimalDFTSize(eh + 2 * max_radius)
        fw = cv2.getOptimalDFTSize(ew + 2 * max_radius)

        padded = np.zeros((fh, fw, 3), dtype=np.float32)
        padded[:eh, :ew] = extended
        spectrum = np.fft.rfft2(padded, axes=(0, 1))

        results = []
        for radius in radii:
            _check_cancelled(should_cancel)
            kernel = BokehEngine._disc_kernel(radius, blade_count, spherical)
            kh, kw = kernel.shape
            kernel_padded = np.zeros((fh, fw), dtype=np.float32)
            kernel_padded[:kh, :kw] = kernel
            kernel_spectrum = np.fft.rfft2(kernel_padded)
            blurred = np.fft.irfft2(
                spectrum * kernel_spectrum[..., None],
                s=(fh, fw), axes=(0, 1),
            )
            start = pad + radius
            results.append(
                blurred[start:start + h, start:start + w].astype(np.float32)
            )
        return results

    @staticmethod
    def _masked_blur(image_f, weight, sigma):
        """Blur only the allowed pixels (normalized convolution).

        The weight is 0 on the sharp subject. Renormalising by the blurred
        weight means the subject's colours never reach the background plate.
        """
        weight = weight.astype(np.float32)
        w3 = weight[..., None]
        # Blur colour and weight together in one pass (RGBA float image).
        stacked = np.empty((*image_f.shape[:2], 4), dtype=np.float32)
        stacked[..., :3] = image_f * w3
        stacked[..., 3] = weight
        blurred = _fast_blur(stacked, sigma)
        num = blurred[..., :3]
        den = blurred[..., 3]

        total = float(weight.sum())
        if total > 1e-3:
            mean = (image_f * w3).sum(axis=(0, 1)) / total
        else:
            mean = image_f.mean(axis=(0, 1))

        out = num / np.maximum(den, 1e-3)[..., None]
        unsafe = den <= 1e-3
        if bool(unsafe.any()):
            out[unsafe] = mean
        return out

    def process(self, image, depth, settings, mode=MODE_AI, return_layers=False, *, should_cancel=None):
        _check_cancelled(should_cancel)
        if image is None:
            return None

        h, w = image.shape[:2]
        center_mode = depth is None or mode == MODE_CENTER
        if center_mode:
            depth = self._center_depth(h, w)
        else:
            if depth.shape[:2] != (h, w):
                depth = cv2.resize(depth.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
            depth = depth.astype(np.float32)
            dmin, dmax = float(depth.min()), float(depth.max())
            if dmax > dmin:
                depth = (depth - dmin) / (dmax - dmin)

        coc = self._make_coc(depth, settings, one_sided=center_mode)
        full_range = bool(settings.full_luminance_bokeh)
        if full_range and not np.any(coc):
            if return_layers:
                return image.copy(), np.zeros_like(image), np.zeros((h, w), np.uint8)
            return image.copy()
        _check_cancelled(should_cancel)

        # Soft mask of the in-focus subject. It must not be allowed to
        # contribute colour to the defocused background, otherwise a full-frame
        # blur smears a coloured halo around the subject.
        threshold = 2.6 if settings.protect_subject else 1.9
        sharp = np.clip(1.0 - coc * threshold, 0.0, 1.0)
        sharp = sharp * sharp * (3.0 - 2.0 * sharp)

        # Subject-edge adjustment. A monocular depth model is least reliable
        # right at a silhouette, so let the user grow/shrink the sharp region
        # (to pull the halo in or out) and feather it (to blend the boundary).
        short_side = max(1, min(h, w))
        edge_px = int(round(
            float(np.clip(getattr(settings, "subject_expand", 0.0), -1.0, 1.0))
            * short_side * 0.012
        ))
        edge_px = int(np.clip(edge_px, -30, 30))
        if edge_px:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (abs(edge_px) * 2 + 1, abs(edge_px) * 2 + 1)
            )
            sharp = cv2.dilate(sharp, kernel) if edge_px > 0 else cv2.erode(sharp, kernel)
        feather = float(np.clip(getattr(settings, "subject_feather", 0.0), 0.0, 1.0))
        if feather > 1e-3:
            sharp = cv2.GaussianBlur(
                sharp.astype(np.float32), (0, 0), sigmaX=feather * short_side * 0.010
            )
        np.clip(sharp, 0.0, 1.0, out=sharp)

        # Remove a small margin around the subject from the background plate as
        # well; otherwise antialiased subject-edge pixels leak a coloured fringe
        # through the (now disc-shaped) defocus convolution.
        expand = max(2, int(min(h, w) * 0.004))
        protect = cv2.dilate(
            sharp,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (expand * 2 + 1, expand * 2 + 1)),
        )
        np.clip(protect, 0.0, 1.0, out=protect)
        _check_cancelled(should_cancel)

        # Defocused background is low-frequency: build it at a bounded
        # resolution, then put the full-resolution sharp subject back on top.
        scale = min(1.0, 1000.0 / max(h, w))
        sw, sh = max(1, int(w * scale)), max(1, int(h * scale))
        if full_range:
            signed_coc = -coc if center_mode else np.copysign(coc, depth - settings.focus)
            del depth, coc
            values = np.arange(256, dtype=np.float32) / 255.0
            lut = np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)
            if scale < 1.0:
                # Decode before area averaging to preserve highlight energy.
                # Convert one channel at a time to avoid a full float RGB copy.
                small = np.empty((sh, sw, 3), np.float32)
                for c in range(3):
                    _check_cancelled(should_cancel)
                    plane = cv2.LUT(image[..., c], lut)
                    small[..., c] = cv2.resize(plane, (sw, sh), interpolation=cv2.INTER_AREA)
                    del plane
                coc_s = cv2.resize(signed_coc, (sw, sh), interpolation=cv2.INTER_AREA)
            else:
                small, coc_s = cv2.LUT(image, lut), signed_coc
            del signed_coc
        elif scale < 1.0:
            small = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
            coc_s = cv2.resize(coc, (sw, sh), interpolation=cv2.INTER_AREA)
        else:
            small, coc_s = image, coc

        if scale < 1.0:
            if full_range:
                np.subtract(1.0, protect, out=protect)
                bg_s = cv2.resize(protect, (sw, sh), interpolation=cv2.INTER_AREA)
            else:
                bg_s = cv2.resize(1.0 - protect, (sw, sh), interpolation=cv2.INTER_AREA)
        else:
            bg_s = 1.0 - protect
        if full_range:
            del protect

        # Shrink the background weight: this removes the partially weighted
        # subject-edge pixels that would otherwise tint the bokeh.
        bg_s = cv2.erode(bg_s, np.ones((3, 3), np.uint8), iterations=2)

        resolution_scale = max(h, w) / 1000.0
        base_radius = 2 + 34 * settings.blur_strength * (
            1.5 / max(settings.aperture, 1.2)
        ) ** 0.40
        max_radius = int(np.clip(base_radius * resolution_scale, 2, 65 * resolution_scale))
        radius_small = max(1.0, max_radius * scale)

        small_f = small.astype(np.float32, copy=False)
        # Build one background-only clean plate: the sharp subject is replaced
        # by a smooth extrapolation of the surrounding background. Ordinary
        # Gaussian blurs can then be used at every scale without ever pulling
        # the subject's colours into the bokeh. The fill is very low-frequency,
        # so it is computed on a quarter-size copy.
        fill_scale = 0.25
        fw, fh = max(1, int(sw * fill_scale)), max(1, int(sh * fill_scale))
        fill_src = cv2.resize(small_f, (fw, fh), interpolation=cv2.INTER_AREA)
        fill_weight = cv2.resize(bg_s, (fw, fh), interpolation=cv2.INTER_AREA)
        # Erode again at quarter resolution so mixed subject/background pixels
        # at the boundary cannot tint the extrapolated background colour.
        fill_weight = cv2.erode(fill_weight, np.ones((3, 3), np.uint8))
        fill = self._masked_blur(fill_src, fill_weight, max(2.0, radius_small * fill_scale * 1.4))
        fill = cv2.resize(fill, (sw, sh), interpolation=cv2.INTER_LINEAR)
        clean = small_f * bg_s[..., None] + fill * (1.0 - bg_s)[..., None]

        if full_range:
            # Treat the size control as footprint area in this mode so the
            # radius grows with its square root. Large settings still allow
            # overlapping discs without overwhelming the entire background.
            size_factor = np.sqrt(max(settings.bokeh_size, 0.0)) * (0.82 + 0.36 * settings.bokeh_strength)
            # CoC already includes aperture and blur strength. Applying them
            # again here would make moderate settings produce tiny discs.
            radius_at_1000 = 26.0 * size_factor * max(1.0, settings.blur_strength)
            # Keep combined extremes bounded without a hard plateau.
            if radius_at_1000 > 80.0:
                radius_at_1000 = 80.0 + 48.0 * -np.expm1((80.0 - radius_at_1000) / 48.0)
            optical_radius = radius_at_1000 * max(sw, sh) / 1000.0
            far, near, near_alpha = self._render_wavefront(clean, coc_s, bg_s, optical_radius, settings, should_cancel)
            return self._composite_wavefront(
                image, sharp, far, near, near_alpha, settings.protect_subject, return_layers, should_cancel,
            )
        else:
            result_small = small_f.copy()
            levels = (0.40, 0.70, 1.00)
            radii = [max(1.0, radius_small * level) for level in levels]
            blurred_levels = self._disc_blur_levels(
                clean, radii, settings.bokeh_blades, settings.spherical, should_cancel,
            )
            for level, blurred in zip(levels, blurred_levels):
                alpha = np.clip((coc_s - (1.0 - level) * 0.20) / max(level + 0.05, 1e-3), 0, 1)
                if float(alpha.max()) < 1e-3:
                    continue
                alpha = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), 1.0)
                result_small *= (1.0 - alpha)[..., None]
                result_small += blurred * alpha[..., None]

        if scale < 1.0:
            result = cv2.resize(result_small, (w, h), interpolation=cv2.INTER_LINEAR)
        else:
            result = result_small

        # Keep the background/effect plate before putting the sharp subject back.
        background_u8 = np.clip(result, 0, 255).astype(np.uint8)
        _check_cancelled(should_cancel)
        effect = self._bokeh_overlay(background_u8, image, coc, settings, protect, should_cancel)

        final = effect.astype(np.float32)
        final *= (1.0 - sharp)[..., None]
        for c in range(3):
            final[:, :, c] += image[:, :, c].astype(np.float32) * sharp
        final = np.clip(final, 0, 255).astype(np.uint8)

        if return_layers:
            alpha = (np.clip(1.0 - sharp, 0.0, 1.0) * 255.0).astype(np.uint8)
            return final, effect, alpha
        return final
