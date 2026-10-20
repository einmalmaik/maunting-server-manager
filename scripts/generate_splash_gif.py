import math
import os
from PIL import Image, ImageDraw, ImageEnhance

base_path = "browser/src-tauri/installer/sidebar_hidpi_preview.png"
if not os.path.exists(base_path):
    base_path = "assets/installer/sidebar_hidpi_preview.png"

base_img = Image.open(base_path).convert("RGBA")
w, h = base_img.size

# Target size: 328 x 628
frames = []
num_frames = 24  # 24 frames for a smooth 1.5s loop

for i in range(num_frames):
    # Phase from 0.0 to 1.0
    phase = i / num_frames
    
    # Create an overlay with subtle glowing pulse
    # Logo center is roughly at (w/2, h*0.38)
    cx, cy = w / 2, h * 0.38
    
    # Breath scale factor
    glow_intensity = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
    
    # Create copy of frame
    frame = base_img.copy()
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    # Outer orbiting spark
    orbit_radius = 85 + 4 * math.sin(phase * 2 * math.pi)
    angle = phase * 2 * math.pi
    spark_x = cx + orbit_radius * math.cos(angle)
    spark_y = cy + orbit_radius * math.sin(angle)
    
    # Draw cyan glow halo around spark
    glow_r = int(14 + 4 * glow_intensity)
    draw.ellipse(
        [spark_x - glow_r, spark_y - glow_r, spark_x + glow_r, spark_y + glow_r],
        fill=(91, 192, 190, int(120 * glow_intensity))
    )
    # Bright center spark
    draw.ellipse(
        [spark_x - 3, spark_y - 3, spark_x + 3, spark_y + 3],
        fill=(220, 255, 255, 240)
    )
    
    # Opposite counter spark
    opp_angle = angle + math.pi
    opp_x = cx + orbit_radius * math.cos(opp_angle)
    opp_y = cy + orbit_radius * math.sin(opp_angle)
    draw.ellipse(
        [opp_x - glow_r + 2, opp_y - glow_r + 2, opp_x + glow_r - 2, opp_y + glow_r - 2],
        fill=(91, 192, 190, int(90 * glow_intensity))
    )
    draw.ellipse(
        [opp_x - 2, opp_y - 2, opp_x + 2, opp_y + 2],
        fill=(200, 250, 255, 220)
    )
    
    # Central subtle logo pulse
    logo_glow_r = int(70 + 8 * glow_intensity)
    draw.ellipse(
        [cx - logo_glow_r, cy - logo_glow_r, cx + logo_glow_r, cy + logo_glow_r],
        fill=(91, 192, 190, int(25 * glow_intensity))
    )
    
    # Composite overlay
    composed = Image.alpha_composite(frame, overlay)
    
    # Convert to RGB (opaque black background as required for Velopack)
    rgb_frame = Image.new("RGB", (w, h), (7, 12, 17))
    rgb_frame.paste(composed, mask=composed.split()[3])
    
    frames.append(rgb_frame)

out_gif = "browser/src-tauri/installer/splash_animated.gif"
frames[0].save(
    out_gif,
    save_all=True,
    append_images=frames[1:],
    duration=60,  # ~16 fps -> 1.44 sec loop
    loop=0,
    optimize=True
)

# Also copy to assets and smart-system
os.makedirs("smart-system/src-tauri/installer", exist_ok=True)
os.makedirs("assets/installer", exist_ok=True)
frames[0].save(
    "smart-system/src-tauri/installer/splash_animated.gif",
    save_all=True,
    append_images=frames[1:],
    duration=60,
    loop=0,
    optimize=True
)
frames[0].save(
    "assets/installer/splash_animated.gif",
    save_all=True,
    append_images=frames[1:],
    duration=60,
    loop=0,
    optimize=True
)
print("Animated GIF generated successfully:", out_gif)
