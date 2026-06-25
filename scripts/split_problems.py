"""
split_problems.py — Tách ảnh đề bài vào subfolder riêng.

Usage:
    python split_problems.py <folder_chứa_ảnh>
    python split_problems.py .   (chạy tại folder hiện tại)

Kết quả: mỗi ảnh → Bai1/..., Bai2/..., Bai3/...
"""

import sys
import shutil
from pathlib import Path

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".pdf"}

def split(root: Path):
    images = sorted(
        [f for f in root.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED],
        key=lambda f: f.name,
    )

    if not images:
        print("Không tìm thấy ảnh nào trong folder.")
        return

    print(f"Tìm thấy {len(images)} ảnh, bắt đầu tách...")

    for i, img in enumerate(images, start=1):
        dest_dir = root / f"Bai{i}"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / img.name
        shutil.move(str(img), str(dest))
        print(f"  [{i:02d}] {img.name} → Bai{i}/")

    print(f"\n✅ Xong! Đã tạo {len(images)} folder.")

if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    if not folder.is_dir():
        print(f"Không tìm thấy folder: {folder}")
        sys.exit(1)
    split(folder)