import sys, os, math, hashlib
from collections import Counter

def entropy(data: bytes) -> float:
    """How random the bytes look. Plain text is low (~4-5), encrypted is close to 8."""
    if not data:
        return 0.0
    counts = Counter(data)
    return -sum((c / len(data)) * math.log2(c / len(data)) for c in counts.values())

def inspect_file(path: str, label: str, nbytes: int = 4096):
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return
    with open(path, "rb") as f:
        data = f.read(nbytes)

    print(f"\n=== {label} ===")
    print(f"File: {path}  ({os.path.getsize(path)} bytes)")
    print(f"First 64 bytes (hex): {data[:64].hex()}")
    print(f"Readable text: {''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:200])}")
    print(f"Randomness score: {entropy(data):.3f} (max 8.0)")
    print(f"MD5: {hashlib.md5(data).hexdigest()}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python inspect_files.py <path-to-file> <label>")
        sys.exit(1)
    inspect_file(sys.argv[1], sys.argv[2])