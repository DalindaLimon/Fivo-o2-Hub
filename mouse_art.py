"""
Pictures of the supported mice, with clickable button badges on top.

Each view is a product photo in data/devices/ with a transparent background
(see tools/clear_background.py). The app crops the displayed area to the
mouse and overlays numbered badges that act as buttons.

To add a model or replace a photo, drop an image (PNG/JPG) into
data/devices/ and describe it with an `Art`: which region of the image to
show (`crop`) and where each button is (`hotspots`, in the image's own pixel
coordinates). Register it in ART_BY_MODEL.
"""

import io
import os
from dataclasses import dataclass, field

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Rsvg", "2.0")
from gi.repository import Gdk, GLib, Gtk, Rsvg

import appinfo

PHOTO_DIR = os.path.join(appinfo.DATA_DIR, "devices")


@dataclass
class Hotspot:
    """A clickable badge drawn over a physical button."""
    index: int          # onboard button index
    label: str          # short text in the badge ("G4")
    x: float            # position in the photo's pixel coordinates
    y: float


@dataclass
class Art:
    """One photo of a mouse: the file, the region to show, and its button hotspots."""
    photo: str                                  # file name in data/devices/
    crop: tuple                                 # (x, y, w, h) of the photo to display
    hotspots: list = field(default_factory=list)
    badge_scale: float = 1.0                    # badge radius = 14 px * scale (photo pixels)

    @property
    def width(self):
        """Displayed width in photo pixels (the crop width)."""
        return self.crop[2]

    @property
    def height(self):
        """Displayed height in photo pixels (the crop height)."""
        return self.crop[3]

    @property
    def path(self):
        """Absolute path of the photo file."""
        return os.path.join(PHOTO_DIR, self.photo)


# ---------------------------------------------------------------------------------------
# G502
# ---------------------------------------------------------------------------------------

G502_TOP = Art(
    photo="g502-top.png", crop=(150, 70, 260, 380), badge_scale=0.62,
    hotspots=[
        Hotspot(0, "G1", 240, 200),
        Hotspot(1, "G2", 352, 200),
        Hotspot(2, "G3", 297, 140),
        Hotspot(10, "◀", 269, 172),
        Hotspot(9, "▶", 324, 172),
        Hotspot(8, "G9", 297, 251),   # the button with the round icon above it is the wheel-mode toggle
        Hotspot(7, "G8", 210, 160),
        Hotspot(6, "G7", 207, 186),
    ],
)

G502_SIDE = Art(
    photo="g502-side.png", crop=(0, 185, 378, 160), badge_scale=0.62,
    hotspots=[
        Hotspot(2, "G3", 105, 212),
        Hotspot(0, "G1", 175, 214),
        Hotspot(7, "G8", 128, 247),
        Hotspot(6, "G7", 190, 229),
        Hotspot(4, "G5", 160, 258),
        Hotspot(3, "G4", 215, 256),
        Hotspot(5, "G6", 147, 292),
    ],
)

# Each model: {view name: Art}. "top" is the default view.
ART_BY_MODEL = {"G502": {"top": G502_TOP, "side": G502_SIDE}}


def views_for(device_name):
    """{view name: Art} for a device name, or {} if we have no pictures for it."""
    for key, views in ART_BY_MODEL.items():
        if key.lower() in device_name.lower():
            return {name: art for name, art in views.items() if os.path.exists(art.path)}
    return {}


def art_for(device_name, view="top"):
    """Picture for a device name and view ("top"/"side"), or None if we have none."""
    return views_for(device_name).get(view)


_surfaces = {}


def photo_surface(path):
    """The photo as a cairo surface, loaded once (any format GDK can read)."""
    if path not in _surfaces:
        png = Gdk.Texture.new_from_filename(path).save_to_png_bytes().get_data()
        _surfaces[path] = cairo.ImageSurface.create_from_png(io.BytesIO(png))
    return _surfaces[path]


def _hex(rgb):
    """(r, g, b) floats/ints -> '#rrggbb', clamped to 0..255."""
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c))) for c in rgb))


def badges_svg(art, highlight=None, hover=None, accent="#3584e4"):
    """SVG overlay with one badge per hotspot, in the photo's coordinate space."""
    x0, y0, w, h = art.crop
    k = art.badge_scale
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {w} {h}">']
    for hs in art.hotspots:
        active = hs.index in (highlight, hover)
        fill = accent if active else "#ffffff"
        text = "#ffffff" if active else "#1e1e1e"
        if active:
            parts.append(f'<circle cx="{hs.x}" cy="{hs.y}" r="{20 * k}" fill="{accent}" opacity="0.35"/>')
        parts.append(
            f'<circle cx="{hs.x}" cy="{hs.y}" r="{14 * k}" fill="{fill}" stroke="#000" '
            f'stroke-opacity="0.45" stroke-width="{1.6 * k}"/>'
            f'<text x="{hs.x}" y="{hs.y + 4.4 * k}" text-anchor="middle" '
            f'font-family="Cantarell, sans-serif" font-size="{12 * k}" font-weight="700" '
            f'fill="{text}">{GLib.markup_escape_text(hs.label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


class MouseView(Gtk.DrawingArea):
    """A mouse photo, optionally with clickable button badges.

    Callbacks (plain attributes, set by the page):
      on_activate(index)  — a badge was clicked
      on_hover(index|None)
    """

    def __init__(self, art, interactive=False, size=(260, 400)):
        super().__init__(content_width=size[0], content_height=size[1], hexpand=True)
        self.art = art
        self.interactive = interactive
        self.highlight = None
        self.hover = None
        self.on_activate = None
        self.on_hover = None
        self._overlay = None
        self._dirty = True
        self._xf = (1.0, 0.0, 0.0)  # scale, offset x, offset y
        self.set_draw_func(self._draw)
        if interactive:
            motion = Gtk.EventControllerMotion()
            motion.connect("motion", self._on_motion)
            motion.connect("leave", lambda *_: self._set_hover(None))
            self.add_controller(motion)
            click = Gtk.GestureClick()
            click.connect("released", self._on_click)
            self.add_controller(click)

    # state ---------------------------------------------------------------------------
    def set_highlight(self, index):
        """Emphasise one button's badge (e.g. while its list row is hovered); None clears."""
        if index != self.highlight:
            self.highlight = index
            self._dirty = True
            self.queue_draw()

    def _set_hover(self, index):
        """Update the hovered badge, cursor and callbacks."""
        if index != self.hover:
            self.hover = index
            self._dirty = True
            self.set_cursor_from_name("pointer" if index is not None else None)
            self.queue_draw()
            if self.on_hover:
                self.on_hover(index)

    # drawing ---------------------------------------------------------------------------
    def _accent(self):
        """The desktop accent colour as hex, for badges."""
        try:
            from gi.repository import Adw
            c = Adw.StyleManager.get_default().get_accent_color_rgba()
            return _hex((c.red * 255, c.green * 255, c.blue * 255))
        except Exception:  # noqa: BLE001 - older libadwaita
            return "#3584e4"

    def _draw(self, _area, cr, w, h):
        """Draw func: the (transparent-background) photo crop, scaled to fit, then the badges."""
        x0, y0, cw, ch = self.art.crop
        scale = min(w / cw, h / ch)
        ox, oy = (w - cw * scale) / 2, (h - ch * scale) / 2
        self._xf = (scale, ox, oy)

        cr.save()
        cr.rectangle(ox, oy, cw * scale, ch * scale)
        cr.clip()
        cr.translate(ox, oy)
        cr.scale(scale, scale)
        cr.set_source_surface(photo_surface(self.art.path), -x0, -y0)
        cr.get_source().set_filter(cairo.FILTER_BEST)
        cr.paint()
        cr.restore()

        if self.interactive:
            if self._dirty or self._overlay is None:
                svg = badges_svg(self.art, self.highlight, self.hover, self._accent())
                self._overlay = Rsvg.Handle.new_from_data(svg.encode())
                self._dirty = False
            vp = Rsvg.Rectangle()
            vp.x, vp.y, vp.width, vp.height = ox, oy, cw * scale, ch * scale
            self._overlay.render_document(cr, vp)

    # input -------------------------------------------------------------------------------
    def _hit(self, x, y):
        """Hotspot index under widget coordinates (x, y), or None."""
        scale, ox, oy = self._xf
        px = (x - ox) / scale + self.art.crop[0]
        py = (y - oy) / scale + self.art.crop[1]
        radius = 20 * self.art.badge_scale
        for hs in self.art.hotspots:
            if (px - hs.x) ** 2 + (py - hs.y) ** 2 <= radius ** 2:
                return hs.index
        return None

    def _on_motion(self, _ctl, x, y):
        """Pointer moved: update hover."""
        self._set_hover(self._hit(x, y))

    def _on_click(self, _g, _n, x, y):
        """Click released on a badge: call on_activate(index)."""
        idx = self._hit(x, y)
        if idx is not None and self.on_activate:
            self.on_activate(idx)
