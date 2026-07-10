"""Script to generate the official TurboFix branding logos (SVG & PNG).

Outputs:
  - logo.svg (vector graphic)
  - logo.png (high-res raster image)

Requires 'pillow' for PNG generation:
    pip install pillow
"""

import sys
from pathlib import Path

SVG_CONTENT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 120" width="100%" height="100%">
  <!-- Definition of gradients -->
  <defs>
    <linearGradient id="brandGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#22a35a" />
      <stop offset="100%" stop-color="#125c31" />
    </linearGradient>
    <linearGradient id="boltGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#f8fafc" />
      <stop offset="100%" stop-color="#cbd5e1" />
    </linearGradient>
  </defs>

  <!-- Background -->
  <rect width="100%" height="100%" fill="#0f172a" rx="16" />

  <!-- Logo Icon: Stylized Gear/Shield + Lightning Bolt -->
  <g transform="translate(25, 20)">
    <!-- Shield shape -->
    <path d="M 40 5 C 40 5, 75 15, 75 40 C 75 65, 40 75, 40 75 C 40 75, 5 65, 5 40 C 5 15, 40 5, 40 5 Z" fill="url(#brandGrad)" />
    <!-- Lightning Bolt -->
    <path d="M 43 15 L 23 45 L 38 45 L 33 65 L 57 35 L 42 35 Z" fill="url(#boltGrad)" />
  </g>

  <!-- Brand Typography -->
  <text x="120" y="74" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="44" font-weight="800" fill="#ffffff" letter-spacing="0.5">Turbo<tspan fill="#22a35a">Fix</tspan></text>
  <text x="122" y="96" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="14" font-weight="600" fill="#64748b" letter-spacing="1.5">SMART MAINTENANCE</text>
</svg>
"""

def generate_logo():
    # 1. Generate SVG Logo
    svg_path = Path("logo.svg")
    svg_path.write_text(SVG_CONTENT, encoding="utf-8")
    print(f"Generated Vector Logo: {svg_path.resolve()}")

    # 2. Generate PNG Logo (requires Pillow)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("\nNote: 'pillow' library is not installed. To generate the high-res PNG logo, run:")
        print("  pip install pillow")
        print("And re-run this script.")
        return

    # Dimensions
    width, height = 1000, 240
    img = Image.new("RGBA", (width, height), (15, 23, 42, 255)) # Dark slate background (#0f172a)
    draw = ImageDraw.Draw(img)

    # Draw green shield
    shield_pts = [(160, 40), (230, 60), (230, 110), (160, 170), (90, 110), (90, 60)]
    draw.polygon(shield_pts, fill=(34, 163, 90, 255))
    
    # Draw white lightning bolt
    bolt_pts = [(166, 60), (126, 120), (156, 120), (146, 160), (196, 100), (166, 100)]
    draw.polygon(bolt_pts, fill=(248, 250, 252, 255))

    # Text drawing fallback (using default font if custom font not found)
    try:
        font_path = "Arial Bold.ttf"
        font_large = ImageFont.truetype(font_path, 88)
        font_small = ImageFont.truetype(font_path, 28)
    except IOError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Draw Text
    draw.text((260, 60), "Turbo", fill=(255, 255, 255, 255), font=font_large)
    draw.text((505, 60), "Fix", fill=(34, 163, 90, 255), font=font_large)
    draw.text((262, 160), "SMART MAINTENANCE", fill=(100, 116, 139, 255), font=font_small)

    png_path = Path("logo.png")
    img.save(png_path, "PNG")
    print(f"Generated High-Res PNG Logo: {png_path.resolve()}")

if __name__ == "__main__":
    generate_logo()
