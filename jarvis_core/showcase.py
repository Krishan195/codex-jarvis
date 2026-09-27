"""Native desktop information card for visual Jarvis answers."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
import subprocess
import sys
import textwrap


def _download_image(url: str):
    from PIL import Image
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=12) as r:
        data = r.read(12 * 1024 * 1024)
    image = Image.open(BytesIO(data)).convert("RGB")
    image.thumbnail((760, 430))
    return image


def show_window(title: str, description: str, image_url: str = "",
                source_url: str = "") -> None:
    import tkinter as tk
    from tkinter import ttk
    from PIL import ImageTk

    root = tk.Tk()
    root.title(f"Jarvis - {title}")
    root.geometry("820x720")
    root.minsize(680, 500)

    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame, text=title, font=("Sans", 22, "bold"),
        wraplength=760, justify="left",
    ).pack(anchor="w", pady=(0, 14))

    photo = None
    if image_url:
        try:
            image = _download_image(image_url)
            photo = ImageTk.PhotoImage(image)
            img_label = ttk.Label(frame, image=photo)
            img_label.image = photo
            img_label.pack(anchor="center", pady=(0, 18))
        except Exception:
            pass

    body = tk.Text(frame, wrap="word", height=10, borderwidth=0,
                   font=("Sans", 12))
    body.insert("1.0", description.strip())
    body.configure(state="disabled")
    body.pack(fill="both", expand=True)

    if source_url:
        def open_source():
            subprocess.Popen(["xdg-open", source_url],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        ttk.Button(frame, text="Open source", command=open_source).pack(
            anchor="e", pady=(14, 0)
        )

    root.attributes("-topmost", True)
    root.after(700, lambda: root.attributes("-topmost", False))
    root.mainloop()


def spawn(title: str, description: str, image_url: str = "",
          source_url: str = "") -> None:
    args = [
        sys.executable, "-m", "jarvis_core.showcase",
        "--title", title,
        "--description", description,
    ]
    if image_url:
        args += ["--image-url", image_url]
    if source_url:
        args += ["--source-url", source_url]
    subprocess.Popen(args, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--image-url", default="")
    p.add_argument("--source-url", default="")
    a = p.parse_args()
    show_window(a.title, a.description, a.image_url, a.source_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
