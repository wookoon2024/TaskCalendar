import os
from PIL import Image

def main():
    src_path = r"c:\Pro1\캘린더\taskcalendar\assets\app_icon.ico"
    dest_dir = r"c:\Pro1\캘린더\chrome_extension"
    
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    try:
        if os.path.exists(src_path):
            with Image.open(src_path) as img:
                img = img.convert("RGBA")
                for size in [16, 48, 128]:
                    resized = img.resize((size, size), Image.Resampling.LANCZOS)
                    resized.save(os.path.join(dest_dir, f"icon{size}.png"), "PNG")
            print("Icons generated successfully.")
        else:
            print(f"Source icon not found at {src_path}. Creating placeholder icons.")
            for size in [16, 48, 128]:
                img = Image.new("RGBA", (size, size), (108, 92, 231, 255))
                img.save(os.path.join(dest_dir, f"icon{size}.png"), "PNG")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
