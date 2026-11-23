"""Estimate compact light sources before the depth-dependent optical spread."""

from math import sqrt

import cv2
import numpy as np


def reconstruct_bokeh_sources(linear, coc, background, radius, settings):
    """Partly concentrate pre-blurred reflections and estimate their lost SDR energy.

    Move the positive RGB residual within each compact feature to a smaller
    source footprint. Broad and elongated features retain their original
    shape. The recovered light is rendered by the caller's ordinary PSF.
    """
    strength = float(np.clip(settings.bokeh_strength, 0.0, 2.5))
    if strength <= 0.0 or radius < 3.0:
        return linear

    h, w = coc.shape
    unit = max(h, w) / 1000.0
    local_scale = max(2.5, min(16.0 * unit, radius * 0.42))
    # The cutoff is local relative contrast, never image brightness or rank.
    # Keep low-contrast ripples and focus-mask boundaries out of the detector.
    density = float(np.clip(settings.bokeh_density, 0.0, 2.0))
    contrast_floor = 0.08 - 0.035 * min(density, 1.0) - 0.027 * max(density - 1.0, 0.0)
    defocused = (radius * coc >= 3.0) & (background > 0.5)
    if not np.any(defocused):
        return linear

    luminance = linear @ np.float32([0.2126, 0.7152, 0.0722])
    detail = cv2.GaussianBlur(luminance, (0, 0), max(0.65, unit))
    result = linear
    remaining = None
    min_area = max(2, int(3 * unit**2))
    fraction = (0.82 * min(strength, 1.0) * np.clip(coc * 1.7, 0.0, 1.0)) * background
    # The smaller neighborhood can find weak, closely spaced reflections
    # that would be hidden in the broad background estimate of a bright one.
    scales = (local_scale, local_scale / 3.0) if local_scale >= 6.0 else (local_scale,)
    for scale in scales:
        local = cv2.GaussianBlur(detail, (0, 0), scale)
        contrast = np.maximum(detail - local, 0.0) / (detail + local + 0.003)
        eligible = (contrast > contrast_floor) & defocused
        count, labels, stats, _ = cv2.connectedComponentsWithStats(eligible.astype(np.uint8))
        if count <= 1:
            continue
        width, height, area = stats[1:, 2:].T
        extent = np.maximum(width, height)
        candidates = 1 + np.flatnonzero(
            (area >= min_area) & (extent <= 3.1 * scale)
            & (extent <= 2.7 * np.minimum(width, height))
        )
        if not candidates.size:
            continue
        if remaining is None:
            result = linear.copy()
            remaining = defocused.copy()
        ambient = cv2.GaussianBlur(linear, (0, 0), scale)
        _concentrate_components(
            linear, ambient, fraction, labels, stats, candidates, remaining,
            result, strength, settings,
        )
    return result


def _concentrate_components(
    linear, ambient, fraction, labels, stats, candidates, remaining,
    result, strength, settings,
):
    threshold = float(settings.highlight_threshold) if settings.highlight_rolloff else None
    threshold_range = max(1.0 - threshold, 1e-3) if threshold is not None else 1.0
    coords = np.arange(max(linear.shape[:2]))
    for label in candidates:
        x, y, width, height, _ = stats[label]
        sy, sx = slice(y, y + height), slice(x, x + width)
        member = labels[sy, sx] == label
        remaining_patch = remaining[sy, sx]
        if (member & ~remaining_patch).any():
            continue
        pixels = linear[sy, sx]
        residual = np.maximum(pixels - ambient[sy, sx], 0.0)
        residual *= (fraction[sy, sx] * member)[..., None]
        mass = residual.sum(axis=2)
        total = float(mass.sum())
        if total <= 1e-7:
            continue

        yy, xx = coords[:height, None], coords[None, :width]
        center_y = float((yy * mass).sum() / total)
        center_x = float((xx * mass).sum() / total)
        distance = (xx - center_x)**2 + (yy - center_y)**2
        spread = float((distance * mass).sum() / total)
        sigma = max(0.55, 0.28 * sqrt(spread))
        profile = np.exp(-0.5 * distance / sigma**2) * member
        profile_sum = float(profile.sum())
        if profile_sum <= 1e-8:
            continue

        # Redistributing the residual conserves its measured energy. Allow a
        # bounded additional estimate for the loss from an SDR source capture.
        peak = float(pixels[member].max())
        recovery = (1.3 + 3.0 * peak**2) * strength
        if threshold is not None:
            display_peak = 1.055 * peak ** (1.0 / 2.4) - 0.055 if peak > 0.0031308 else 12.92 * peak
            excess = min(max((display_peak - threshold) / threshold_range, 0.0), 1.0)
            recovery *= np.float64(1.0 - 0.45 * excess)
        recovered_flux = residual.sum(axis=(0, 1)) * (1.0 + recovery)
        result[sy, sx] += (profile / profile_sum)[..., None] * recovered_flux - residual
        remaining_patch &= ~member
