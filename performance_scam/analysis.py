"""
Person 3 - Compare unencrypted vs encrypted benchmark results.

Reads performance/results/raw_*.csv (written by benchmark.py) and produces:
  performance/results/comparison.csv   overhead per query/mode with 95% confidence interval
  performance/results/summary.md       the same tables in Markdown (pasted into analysis.md)
  performance/charts.png               visual comparison

Overhead % = (encrypted - baseline) / baseline * 100.
95% CI of the overhead: bootstrap over the 10 per-run means of each server (10,000 resamples).
"Significant" = the CI does not include 0, i.e. the difference is larger than run-to-run noise.

Usage (from the repo root):  python performance/analysis.py
"""
import csv
import json
import math
import os
import statistics

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
CHART = os.path.join(HERE, "charts.png")
RNG = np.random.default_rng(42)

# palette (validated reference palette, light mode)
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
C_BASE, C_ENC = "#2a78d6", "#eb6834"      # categorical slot 1 / slot 2
C_HOT, C_COLD = "#6250d6", "#1baf7a"     # violet / aqua: hot vs cold (never reuse blue/orange here)


def read(name):
    path = os.path.join(RESULTS, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def bootstrap_overhead(base, enc, n=10_000):
    base, enc = np.asarray(base, float), np.asarray(enc, float)
    b = base[RNG.integers(0, len(base), (n, len(base)))].mean(axis=1)
    e = enc[RNG.integers(0, len(enc), (n, len(enc)))].mean(axis=1)
    ratio = (e - b) / b * 100
    return float(np.percentile(ratio, 2.5)), float(np.percentile(ratio, 97.5))


def compare_latency(raw):
    """raw rows -> list of comparison dicts, ordered as in the file."""
    order, out = [], []
    for r in raw:
        if r["query"] not in order:
            order.append(r["query"])
    for q in order:
        rows = [r for r in raw if r["query"] == q]
        base = [float(r["mean_ms"]) for r in rows if r["server"] == "baseline"]
        enc = [float(r["mean_ms"]) for r in rows if r["server"] == "encrypted"]
        if not base or not enc:
            continue
        bm, em = statistics.mean(base), statistics.mean(enc)
        lo, hi = bootstrap_overhead(base, enc)
        out.append({
            "mode": rows[0]["mode"], "query": q, "category": rows[0]["category"],
            "baseline_ms": bm, "baseline_sd": statistics.stdev(base) if len(base) > 1 else 0.0,
            "encrypted_ms": em, "encrypted_sd": statistics.stdev(enc) if len(enc) > 1 else 0.0,
            "overhead_pct": (em - bm) / bm * 100, "ci_low_pct": lo, "ci_high_pct": hi,
            "significant": not (lo <= 0 <= hi), "runs": min(len(base), len(enc)),
        })
    return out


def compare_throughput(raw):
    out = []
    for q in dict.fromkeys(r["query"] for r in raw):
        rows = [r for r in raw if r["query"] == q]
        base = [float(r["ops_per_sec"]) for r in rows if r["server"] == "baseline"]
        enc = [float(r["ops_per_sec"]) for r in rows if r["server"] == "encrypted"]
        bm, em = statistics.mean(base), statistics.mean(enc)
        lo, hi = bootstrap_overhead(base, enc)  # here: % change in ops/s
        bl = statistics.mean(float(r["p95_ms"]) for r in rows if r["server"] == "baseline")
        el = statistics.mean(float(r["p95_ms"]) for r in rows if r["server"] == "encrypted")
        out.append({"mode": "throughput", "query": q, "category": rows[0]["category"],
                    "threads": int(rows[0]["threads"]),
                    "baseline_ops": bm, "baseline_sd": statistics.stdev(base) if len(base) > 1 else 0,
                    "encrypted_ops": em, "encrypted_sd": statistics.stdev(enc) if len(enc) > 1 else 0,
                    "change_pct": (em - bm) / bm * 100, "ci_low_pct": lo, "ci_high_pct": hi,
                    "significant": not (lo <= 0 <= hi),
                    "baseline_p95_ms": bl, "encrypted_p95_ms": el, "rounds": min(len(base), len(enc)),
                    "errors": sum(int(r["errors"]) for r in rows)})
    return out


def tp_settings():
    path = os.path.join(RESULTS, "environment_throughput.json")
    if not os.path.exists(path):
        return "?", "?"
    with open(path) as f:
        a = json.load(f)["args"]
    return a.get("tp_seconds"), a.get("tp_rounds")


def geo_mean_overhead(rows):
    ratios = [r["encrypted_ms"] / r["baseline_ms"] for r in rows]
    return (math.exp(statistics.mean(math.log(x) for x in ratios)) - 1) * 100 if ratios else float("nan")


def fmt_ms(x):
    return f"{x:,.3f}" if x < 10 else f"{x:,.1f}"


def tablespace_sizes():
    path = os.path.join(RESULTS, "environment_hot.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        env = json.load(f)
    sizes = {}
    for s in ("baseline", "encrypted"):
        for name, v in env["servers"][s]["tablespace_bytes"].items():
            sizes.setdefault(name.split("/")[-1], {})[s] = v["file_size"]
    return env, sizes


def write_outputs(lat, tp):
    # ---- CSV
    fields = ["mode", "query", "category", "baseline_ms", "baseline_sd", "encrypted_ms", "encrypted_sd",
              "overhead_pct", "ci_low_pct", "ci_high_pct", "significant", "runs"]
    with open(os.path.join(RESULTS, "comparison.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in lat:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in fields})
        for r in tp:  # throughput rows: values are ops/s, overhead = % change in ops/s
            w.writerow({"mode": "throughput", "query": r["query"], "category": r["category"] + " (ops/s)",
                        "baseline_ms": round(r["baseline_ops"], 1), "baseline_sd": round(r["baseline_sd"], 1),
                        "encrypted_ms": round(r["encrypted_ops"], 1), "encrypted_sd": round(r["encrypted_sd"], 1),
                        "overhead_pct": round(r["change_pct"], 2), "ci_low_pct": round(r["ci_low_pct"], 2),
                        "ci_high_pct": round(r["ci_high_pct"], 2), "significant": r["significant"],
                        "runs": r["rounds"]})

    # ---- Markdown + console
    lines = []
    for mode, title in (("hot", "Hot cache (1 GB buffer pool, data in memory)"),
                        ("cold", "Cold cache (32 MB buffer pool, pages read from disk and decrypted)"),
                        ("rebuild", "Table rebuild: ALTER TABLE transactions FORCE (1M rows)")):
        rows = [r for r in lat if r["mode"] == mode]
        if not rows:
            continue
        unit = "s" if mode == "rebuild" else "ms"
        div = 1000 if mode == "rebuild" else 1
        lines += [f"### {title}", "",
                  f"| Query | Type | Baseline {unit} (± sd) | Encrypted {unit} (± sd) | Overhead | 95% CI | Significant? |",
                  "|---|---|---:|---:|---:|---:|:---:|"]
        for r in rows:
            lines.append(f"| `{r['query']}` | {r['category']} | {fmt_ms(r['baseline_ms']/div)} ± {fmt_ms(r['baseline_sd']/div)} "
                         f"| {fmt_ms(r['encrypted_ms']/div)} ± {fmt_ms(r['encrypted_sd']/div)} "
                         f"| **{r['overhead_pct']:+.1f}%** | {r['ci_low_pct']:+.1f}% to {r['ci_high_pct']:+.1f}% "
                         f"| {'yes' if r['significant'] else 'no (noise)'} |")
        if mode != "rebuild":
            lines.append(f"| **All queries** | geometric mean | | | **{geo_mean_overhead(rows):+.1f}%** | | |")
        lines.append("")
    if tp:
        secs, rounds = tp_settings()
        lines += [f"### Throughput (concurrent clients, {secs} s per round, {rounds} rounds)", "",
                  "| Test | Clients | Baseline ops/s | Encrypted ops/s | Change | 95% CI | Baseline p95 ms | Encrypted p95 ms | Errors |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in tp:
            lines.append(f"| {r['category']} | {r['threads']} | {r['baseline_ops']:,.0f} | {r['encrypted_ops']:,.0f} "
                         f"| **{r['change_pct']:+.1f}%** | {r['ci_low_pct']:+.1f}% to {r['ci_high_pct']:+.1f}% "
                         f"| {r['baseline_p95_ms']:.3f} | {r['encrypted_p95_ms']:.3f} | {r['errors']} |")
        lines.append("")
    ts = tablespace_sizes()
    if ts:
        _, sizes = ts
        lines += ["### Table file size on disk", "", "| Table | Baseline MB | Encrypted MB | Difference |", "|---|---:|---:|---:|"]
        for t in ("customers", "accounts", "transactions"):
            if t in sizes and len(sizes[t]) == 2:
                b, e = sizes[t]["baseline"] / 2**20, sizes[t]["encrypted"] / 2**20
                lines.append(f"| {t} | {b:.1f} | {e:.1f} | {(e - b) / b * 100:+.1f}% |")
        lines.append("")
    md = "\n".join(lines)
    with open(os.path.join(RESULTS, "summary.md"), "w", encoding="utf-8") as f:
        f.write("<!-- generated by performance/analysis.py - do not edit by hand -->\n\n" + md)
    print(md)


def make_chart(lat, tp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                         "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left"})
    fig = plt.figure(figsize=(16, 11), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.15, 1], hspace=0.42, wspace=0.42)

    def style(ax, grid_axis="x"):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)

    # ---- Panel A: overhead % with 95% CI, hot vs cold
    ax = fig.add_subplot(gs[0, :2])
    style(ax)
    queries = [r["query"] for r in lat if r["mode"] == "hot"]
    y = np.arange(len(queries))
    for mode, color, dy in (("hot", C_HOT, -0.17), ("cold", C_COLD, 0.17)):
        rows = {r["query"]: r for r in lat if r["mode"] == mode}
        for i, q in enumerate(queries):
            if q not in rows:
                continue
            r = rows[q]
            ax.plot([r["ci_low_pct"], r["ci_high_pct"]], [i + dy] * 2, color=color, linewidth=2,
                    solid_capstyle="round")
            ax.scatter(r["overhead_pct"], i + dy, s=64, zorder=3, edgecolor=SURFACE, linewidth=2,
                       color=color if r["significant"] else SURFACE)
            if not r["significant"]:
                ax.scatter(r["overhead_pct"], i + dy, s=40, zorder=4, facecolor=SURFACE, edgecolor=color, linewidth=2)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_yticks(y, queries)
    ax.invert_yaxis()
    ax.set_xlabel("Encryption overhead on latency (%)  ·  0 = no difference, right = slower")
    ax.set_title("A. Latency overhead per query, with 95% confidence interval (10 runs each)")
    ax.legend(handles=[Line2D([], [], color=C_HOT, marker="o", lw=2, label="hot cache (data in memory)"),
                       Line2D([], [], color=C_COLD, marker="o", lw=2, label="cold cache (read + decrypt from disk)"),
                       Line2D([], [], color=INK2, marker="o", lw=0, markerfacecolor=SURFACE,
                              label="hollow = within noise (CI includes 0)")],
              loc="lower right", frameon=False)

    # ---- Panel B: headline numbers
    axb = fig.add_subplot(gs[0, 2])
    axb.axis("off")
    axb.set_title("Headline", loc="left")
    items = []
    for mode, label in (("hot", "Hot cache, all queries"), ("cold", "Cold cache, all queries")):
        rows = [r for r in lat if r["mode"] == mode]
        if rows:
            items.append((f"{geo_mean_overhead(rows):+.1f}%", label + " (geometric mean)"))
    rb = [r for r in lat if r["mode"] == "rebuild"]
    worst = max((r for r in lat if r["mode"] == "hot" and r["significant"]),
                key=lambda r: r["overhead_pct"], default=None)
    if worst:
        items.append((f"{worst['overhead_pct']:+.1f}%", f"Worst query: {worst['query']} (hot, encrypted temp files)"))
    if rb:
        items.append((f"{rb[0]['overhead_pct']:+.1f}%", "Re-encrypting 1M-row table (ALTER … FORCE)"))
    if tp:
        if not any(r["significant"] for r in tp):
            items.append(("no change", "Throughput (1 & 8 clients): differences within noise, 0 errors"))
        else:
            for r in tp:
                if r["significant"]:
                    items.append((f"{r['change_pct']:+.1f}%", f"{r['category']}, {r['threads']} clients"))
    for i, (big, small) in enumerate(items[:5]):
        yy = 0.9 - i * 0.19
        axb.text(0.0, yy, big, fontsize=24, fontweight="bold", color=INK, transform=axb.transAxes, va="center")
        axb.text(0.0, yy - 0.075, small, fontsize=10, color=INK2, transform=axb.transAxes, va="center")

    # ---- Panel C: absolute latency, cold cache, log scale
    axc = fig.add_subplot(gs[1, 0])
    style(axc)
    mode = "cold" if any(r["mode"] == "cold" for r in lat) else "hot"
    rows = [r for r in lat if r["mode"] == mode]
    yy = np.arange(len(rows))
    h = 0.36
    axc.barh(yy - h / 2 - 0.02, [r["baseline_ms"] for r in rows], h, color=C_BASE, label="unencrypted")
    axc.barh(yy + h / 2 + 0.02, [r["encrypted_ms"] for r in rows], h, color=C_ENC, label="encrypted")
    axc.set_xscale("log")
    axc.set_yticks(yy, [r["query"] for r in rows], fontsize=9)
    axc.invert_yaxis()
    axc.set_xlabel("mean latency (ms, log scale)")
    axc.set_title(f"C. Absolute latency, {mode} cache")
    axc.legend(frameon=False, loc="upper right", fontsize=9)

    # ---- Panel D: throughput
    axd = fig.add_subplot(gs[1, 1])
    style(axd, "y")
    if tp:
        x = np.arange(len(tp))
        w = 0.38
        axd.bar(x - w / 2 - 0.02, [r["baseline_ops"] for r in tp], w, yerr=[r["baseline_sd"] for r in tp],
                color=C_BASE, label="unencrypted", error_kw={"ecolor": INK2, "elinewidth": 1})
        axd.bar(x + w / 2 + 0.02, [r["encrypted_ops"] for r in tp], w, yerr=[r["encrypted_sd"] for r in tp],
                color=C_ENC, label="encrypted", error_kw={"ecolor": INK2, "elinewidth": 1})
        axd.set_yscale("log")
        axd.set_xticks(x, [f"{r['category'].split()[0]}\n{r['threads']} client{'s' if r['threads'] > 1 else ''}"
                           for r in tp])
        for i, r in enumerate(tp):
            axd.text(i, max(r["baseline_ops"], r["encrypted_ops"]) * 1.35, f"{r['change_pct']:+.1f}%",
                     ha="center", fontsize=9, color=INK)
        axd.set_ylim(top=max(max(r["baseline_ops"], r["encrypted_ops"]) for r in tp) * 3)
        axd.legend(frameon=False, loc="upper right", fontsize=9)
    axd.set_ylabel("operations per second (log scale)")
    noise = " - all within noise" if tp and not any(r["significant"] for r in tp) else ""
    axd.set_title(f"D. Throughput (mean of rounds, ± sd){noise}")

    # ---- Panel E: rebuild time (seconds only; file size given as a note, different unit)
    axe = fig.add_subplot(gs[1, 2])
    style(axe, "y")
    if rb:
        r = rb[0]
        vals = [r["baseline_ms"] / 1000, r["encrypted_ms"] / 1000]
        sds = [r["baseline_sd"] / 1000, r["encrypted_sd"] / 1000]
        axe.bar([0, 1], vals, 0.6, yerr=sds, color=[C_BASE, C_ENC],
                error_kw={"ecolor": INK2, "elinewidth": 1, "capsize": 4})
        for i, v in enumerate(vals):
            axe.text(i, v + sds[i] + 0.08, f"{v:.2f} s", ha="center", va="bottom", fontsize=10)
        axe.set_xticks([0, 1], ["unencrypted", "encrypted"])
        axe.set_ylim(0, max(vals) * 1.45)
        axe.set_ylabel("seconds (mean of rounds, ± sd)")
        axe.text(0.5, max(vals) * 1.32, f"{r['overhead_pct']:+.1f}% slower", ha="center", fontsize=10,
                 fontweight="bold")
    ts = tablespace_sizes()
    if ts and len(ts[1].get("transactions", {})) == 2:
        s_ = ts[1]["transactions"]
        axe.text(0.5, -0.2, f"File size on disk: {s_['baseline'] / 2**20:.1f} MB vs {s_['encrypted'] / 2**20:.1f} MB "
                 f"(encryption adds no space)", transform=axe.transAxes, ha="center", fontsize=9, color=INK2)
    axe.set_title("E. Rebuild 1M-row table (ALTER … FORCE)")

    fig.suptitle("MariaDB 13.0.2 data-at-rest encryption (HashiCorp Vault keys): performance impact",
                 x=0.06, ha="left", fontsize=15, fontweight="bold", color=INK)
    fig.savefig(CHART, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    print(f"Chart saved to {CHART}")


def main():
    lat = []
    for name in ("raw_hot.csv", "raw_cold.csv", "raw_rebuild.csv"):
        lat += compare_latency(read(name))
    tp = compare_throughput(read("raw_throughput.csv"))
    if not lat and not tp:
        raise SystemExit("No results found - run performance/benchmark.py first.")
    write_outputs(lat, tp)
    make_chart(lat, tp)


if __name__ == "__main__":
    main()
