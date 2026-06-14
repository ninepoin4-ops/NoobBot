"""打包脚本 — 生成 NoobBot.zip"""
import os
import zipfile
import fnmatch

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(os.path.dirname(SRC_DIR), "NoobBot.zip")

EXCLUDE_PATTERNS = [
    "__pycache__", "*.pyc", "*.log",
    "chroma_data", "data", ".env",
    # napcat 体积大（几百 MB 静态资源），按 README 需单独下载，整目录排除
    "napcat",
    "Quick", "Scan",
    # config.yaml 会被 hot_reload 写入真实 api_key/token，禁止打包泄露。
    # 用户拿到 zip 后由 install.bat 生成模板，或手动从模板复制。
    "config/config.yaml",
    "config/personalities",
]


def should_exclude(path: str) -> bool:
    rel = os.path.relpath(path, SRC_DIR)
    rel_norm = rel.replace("\\", "/")
    for pat in EXCLUDE_PATTERNS:
        pat_norm = pat.replace("\\", "/")
        if fnmatch.fnmatch(rel_norm, pat_norm):
            return True
        if rel_norm == pat_norm or rel_norm.startswith(pat_norm.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(os.path.basename(path), pat):
            return True
    return False


def main():
    count = 0
    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(SRC_DIR):
            dirs[:] = [d for d in dirs if not should_exclude(os.path.join(root, d))]
            for f in files:
                fpath = os.path.join(root, f)
                if should_exclude(fpath):
                    continue
                arcname = os.path.relpath(fpath, SRC_DIR)
                zf.write(fpath, arcname)
                count += 1

    size_mb = os.path.getsize(DST) / 1024 / 1024
    print(f"Package created: {DST}")
    print(f"Files: {count}, Size: {size_mb:.0f} MB")
    print(f"\nTo deploy:")
    print(f"  1. Install Python 3.10+")
    print(f"  2. Extract zip anywhere")
    print(f"  3. Run: pip install -r requirements.txt")
    print(f"  4. Edit config/.env - fill API keys")
    print(f"  5. Install QQ NT")
    print(f"  6. Run: python main.py")


if __name__ == "__main__":
    main()
