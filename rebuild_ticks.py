"""
Rebuild a normal ticks_<day>.csv (what the replay tool reads) from the compressed pieces in your ticks repo.

  1. Clone your ticks repo to your PC (GitHub Desktop is fine), then:
  2. python rebuild_ticks.py  <path-to-that-clone>  2026-10-29  [output_folder]

Default output folder: spread_data/ticks next to this script. Repeated header lines (from restarts) are dropped.
"""
import sys, os, glob, gzip

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    repo, day = sys.argv[1], sys.argv[2]
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "spread_data", "ticks")
    pieces = sorted(glob.glob(os.path.join(repo, "ticks", day, f"ticks_{day}_*.csv.gz")))
    if not pieces:
        print(f"No pieces found in {os.path.join(repo, 'ticks', day)}"); sys.exit(1)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"ticks_{day}.csv")
    header, rows = None, 0
    with open(out_path, "wb") as out:
        for pth in pieces:
            with gzip.open(pth, "rb") as g:
                for line in g:
                    if header is None:
                        header = line; out.write(line); continue
                    if line == header:
                        continue
                    out.write(line); rows += 1
    print(f"Wrote {out_path}: {rows:,} rows from {len(pieces)} pieces")

if __name__ == "__main__":
    main()
