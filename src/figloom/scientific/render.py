"""Dispatch actual native scenes and measured plots at final physical size."""
from __future__ import annotations
import math


def figure_dimensions(style):
    value = style.get('layout_width_in', style.get('width', 6.5))
    width = {'column': 3.25, 'wide': 6.5}.get(value, value)
    width = float(width)
    height = float(style.get('height', max(2.5, width * .62)))
    font = float(style.get('font_size', 9))
    if not all(math.isfinite(v) for v in (width, height, font)) or not .5 <= width <= 30 or not .5 <= height <= 30 or not 8 <= font <= 72:
        raise ValueError('Figures require finite dimensions between 0.5 and 30 inches and type between 8 and 72 pt')
    return width, height, font


def render_figure(output_dir, data, style=None, kind='bar'):
    if kind == 'method':
        from figloom.scientific.scene import render_scene
        return render_scene(output_dir, data, style)
    from figloom.scientific.statistical import render_statistical_plot
    return render_statistical_plot(output_dir, data, style, kind)
