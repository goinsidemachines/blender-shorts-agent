"""The made-up kit every kit test uses (never the owner's values)."""

KIT = {"mode": "brand", "style": "toon",
       "palette": {"background": "#EAF2F8", "ink": "#1B2A3A", "card": "#FFFFFF", "text": "#1B2A3A",
                   "accent": "#7A4CE0", "highlight": "#2E7D6B"},
       "categories": {"spark": "#D9483B", "flow": "#3B8ED9", "grip": "#4CAF50"},
       "outline_px": 4, "shadow_px": 6, "corner_px": 20,
       "caption": {"color": "#FFFFFF", "box": "#1B2A3A", "opacity": 1}}  # fmt: skip
DARK = {**KIT, "palette": {**KIT["palette"], "background": "#15181C", "ink": "#F0F0F0", "text": "#F0F0F0",
                           "card": "#262B31"}}  # fmt: skip
