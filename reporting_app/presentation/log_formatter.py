"""Markdown formatting utilities for Process Flow and BigQuery execution logs."""

import json
from typing import Any, Dict, List, Optional


def format_bytes(b: Optional[int]) -> str:
    """Format bytes to human readable format (B, KB, MB, GB, TB)."""
    if b is None or b < 0:
        return "N/A"
    if b < 1024:
        return f"{b} B"
    elif b < 1024**2:
        return f"{b / 1024:.2f} KB"
    elif b < 1024**3:
        return f"{b / (1024**2):.2f} MB"
    elif b < 1024**4:
        return f"{b / (1024**3):.2f} GB"
    else:
        return f"{b / (1024**4):.2f} TB"


def format_duration(seconds: Optional[float]) -> str:
    """Format duration in seconds to readable string."""
    if seconds is None:
        return "N/A"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60.0:
        return f"{seconds:.2f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m {s:.1f}s"


def format_timestamp(iso_str: Optional[str]) -> str:
    """Convert ISO timestamp to readable date/time string."""
    if not iso_str:
        return "N/A"
    return iso_str.replace("T", " ").split(".")[0]


def format_single_node_log(
    log: Any,
    show_query: bool = True,
    show_flow_name: bool = False,
) -> str:
    """Render a single ExecutionLog entry as Markdown."""
    lines = []
    is_success = getattr(log, "status", "") == "SUCCESS"
    status_badge = "✅" if is_success else "❌"
    node_type = getattr(log, "node_type", "query")
    node_name = getattr(log, "node_name", "")
    node_type_label = "Query" if node_type == "query" else "Import Files"

    lines.append(f"## {status_badge} {node_type_label}: `{node_name}`\n")

    # Timing and General Info
    if show_flow_name:
        flow_name = getattr(log, "flow_name", "")
        flow_desc = f"`{flow_name}`" if flow_name and flow_name != "Standalone" else "*Standalone Run*"
        lines.append(f"* **Process Flow:** {flow_desc}")
    lines.append(f"* **Report:** `{getattr(log, 'report_name', '')}`")
    lines.append(f"* **Flow Started:** `{format_timestamp(getattr(log, 'flow_start_time', ''))}`")
    lines.append(f"* **Node Started:** `{format_timestamp(getattr(log, 'node_start_time', ''))}`")
    lines.append(f"* **Full Runtime:** `{format_duration(getattr(log, 'duration_seconds', 0.0))}`")
    bq_dur = getattr(log, "bq_duration_seconds", None)
    if bq_dur is not None:
        lines.append(f"* **BigQuery Cloud Execution Duration:** `{format_duration(bq_dur)}`")
    lines.append(f"* **Status:** **{getattr(log, 'status', '')}**")

    err_msg = getattr(log, "error_message", None)
    if not is_success and err_msg:
        lines.append(f"\n> ⚠️ **Execution Error:**\n> {err_msg}\n")

    if node_type == "query":
        # Data Size and Volume
        lines.append("\n&nbsp;\n")
        lines.append("### 📊 Data Size & Volume")
        lines.append(f"* **Total Bytes Processed:** {format_bytes(getattr(log, 'total_bytes_processed', None))}")
        lines.append(f"* **Total Bytes Billed:** {format_bytes(getattr(log, 'total_bytes_billed', None))}")
        out_rows = getattr(log, "output_rows", None)
        row_cnt_str = f"{out_rows:,} rows" if out_rows is not None else "N/A"
        lines.append(f"* **Output Rows:** {row_cnt_str}")

        # Compute & Cost Efficiency
        lines.append("\n&nbsp;\n")
        lines.append("### ⚡ Compute & Cost Efficiency")
        sm = getattr(log, "slot_millis", None)
        slot_str = f"{sm:,} ms" if sm is not None else "N/A"
        lines.append(f"* **Combined Slot Millis (CPU):** {slot_str}")
        ch = getattr(log, "cache_hit", None)
        cache_str = "True (Cached - $0 cost)" if ch else ("False" if ch is not None else "N/A")
        lines.append(f"* **Cache Hit:** {cache_str}")

        # Exported outputs
        exp_json = getattr(log, "export_details_json", None)
        if exp_json:
            try:
                exports = json.loads(exp_json) if isinstance(exp_json, str) else exp_json
                if exports:
                    lines.append("\n&nbsp;\n")
                    lines.append("### 📁 Exported Files")
                    for exp in exports:
                        fn = exp.get("filename", "output.csv")
                        rc = exp.get("row_count")
                        rc_s = f" ({rc:,} rows)" if rc is not None else ""
                        lines.append(f"* `{fn}`{rc_s}")
            except Exception:
                pass

        # Submitted Query (only shown when show_query is True)
        if show_query:
            sub_query = getattr(log, "submitted_query", None)
            if sub_query:
                clean_query = sub_query.strip()
                lines.append("\n&nbsp;\n")
                lines.append("### Bigquery SQL\n")
                lines.append("```sql")
                lines.append(clean_query)
                lines.append("```\n")

    elif node_type == "import_csv":
        lines.append("\n&nbsp;\n")
        lines.append("### 📥 Import Details")
        out_rows = getattr(log, "output_rows", None)
        if out_rows is not None:
            lines.append(f"* **Rows Loaded:** {out_rows:,} rows")
        tbp = getattr(log, "total_bytes_processed", None)
        if tbp:
            lines.append(f"* **Bytes Processed:** {format_bytes(tbp)}")

        imp_json = getattr(log, "import_details_json", None)
        if imp_json:
            try:
                imports = json.loads(imp_json) if isinstance(imp_json, str) else imp_json
                if imports:
                    lines.append("&nbsp;")
                    lines.append("#### Loaded Sources")
                    for imp in imports:
                        src = imp.get("file_path", "")
                        dst = imp.get("destination_table", "")
                        rc = imp.get("row_count")
                        rc_s = f" — {rc:,} rows" if rc is not None else ""
                        lines.append(f"* Source: `{src}` ➔ Table: `{dst}`{rc_s}")
            except Exception:
                pass

    return "\n".join(lines)


def format_run_session_logs(
    logs: List[Any],
    title: Optional[str] = None,
    show_query: bool = False,
    show_flow_name: bool = False,
) -> str:
    """Render multiple ExecutionLog entries into a combined markdown document."""
    if not logs:
        return "# Execution Log\n\n*No execution logs found for the selected filter.*"

    doc = []
    if title:
        doc.append(f"# {title}\n")
    else:
        first = logs[0]
        f_time = format_timestamp(getattr(first, "flow_start_time", ""))
        doc.append(f"# Execution Run ({f_time})\n")

    doc.append("&nbsp;\n")

    # Overall Summary
    total_duration = sum(getattr(l, "duration_seconds", 0.0) for l in logs)
    total_bq_duration = sum(getattr(l, "bq_duration_seconds", 0.0) or 0.0 for l in logs)
    total_bytes = sum(getattr(l, "total_bytes_processed", 0) or 0 for l in logs)
    total_billed = sum(getattr(l, "total_bytes_billed", 0) or 0 for l in logs)
    all_success = all(getattr(l, "status", "") == "SUCCESS" for l in logs)
    status_icon = "✅ Success" if all_success else "❌ Failed"

    doc.append("### Summary Overview")
    doc.append(f"* **Overall Status:** {status_icon}")
    doc.append(f"* **Total Nodes Executed:** {len(logs)}")
    doc.append(f"* **Full Runtime:** `{format_duration(total_duration)}`")
    if total_bq_duration > 0:
        doc.append(f"* **BigQuery Cloud Execution Duration:** `{format_duration(total_bq_duration)}`")
    if total_bytes > 0:
        doc.append(f"* **Total Bytes Processed:** `{format_bytes(total_bytes)}`")
        doc.append(f"* **Total Bytes Billed:** `{format_bytes(total_billed)}`")
    doc.append("\n---\n")

    for idx, log in enumerate(logs, 1):
        doc.append(f"### Node {idx} of {len(logs)}")
        doc.append(format_single_node_log(log, show_query=show_query, show_flow_name=show_flow_name))
        doc.append("\n---\n")

    return "\n".join(doc)


def format_day_summary_logs(
    date_str: str,
    sessions: List[Dict[str, Any]],
    node_filter: Optional[str] = None,
) -> str:
    """Render a day-level summary with combined totals and per-process-flow run breakdowns."""
    if not sessions:
        return f"# Daily Summary: {date_str}\n\n*No runs recorded for this date.*"

    all_logs = []
    for s in sessions:
        for n in s.get("nodes", []):
            if not node_filter or n.node_name == node_filter:
                all_logs.append(n)

    if not all_logs:
        return f"# Daily Summary: {date_str}\n\n*No nodes match the selected filter.*"

    doc = []
    doc.append(f"# 📅 Daily Execution Summary: `{date_str}`")
    doc.append("&nbsp;")

    total_duration = sum(getattr(l, "duration_seconds", 0.0) for l in all_logs)
    total_bq_duration = sum(getattr(l, "bq_duration_seconds", 0.0) or 0.0 for l in all_logs)
    total_bytes = sum(getattr(l, "total_bytes_processed", 0) or 0 for l in all_logs)
    total_billed = sum(getattr(l, "total_bytes_billed", 0) or 0 for l in all_logs)
    total_slots = sum(getattr(l, "slot_millis", 0) or 0 for l in all_logs)
    total_runs = len(sessions)
    total_nodes = len(all_logs)
    successful_nodes = sum(1 for l in all_logs if getattr(l, "status", "") == "SUCCESS")
    failed_nodes = total_nodes - successful_nodes

    day_totals = [
        "## 📈 Day Totals",
        f"* **Total Runs:** {total_runs}",
        f"* **Total Nodes Executed:** {total_nodes} ({successful_nodes} succeeded, {failed_nodes} failed)",
        f"* **Full Runtime:** `{format_duration(total_duration)}`",
    ]
    if total_bq_duration > 0:
        day_totals.append(f"* **BigQuery Cloud Execution Duration:** `{format_duration(total_bq_duration)}`")
    day_totals.extend([
        f"* **Total Bytes Processed:** `{format_bytes(total_bytes)}`",
        f"* **Total Bytes Billed:** `{format_bytes(total_billed)}`",
    ])
    if total_slots > 0:
        day_totals.append(f"* **Total Slot Millis:** `{total_slots:,} ms`")
    doc.append("\n".join(day_totals))

    doc.append("---")
    doc.append("## 🔄 Process Flow Runs Summary")
    doc.append("&nbsp;")

    matching_sessions = [
        s for s in sessions
        if any(not node_filter or n.node_name == node_filter for n in s.get("nodes", []))
    ]
    total_matching_runs = len(matching_sessions)

    for offset, s in enumerate(matching_sessions):
        s_nodes = [n for n in s.get("nodes", []) if not node_filter or n.node_name == node_filter]
        if not s_nodes:
            continue

        run_num = total_matching_runs - offset
        s_status = s.get("status", "SUCCESS")
        s_badge = "✅" if s_status == "SUCCESS" else "❌"
        raw_ts = s.get("flow_start_time", "")
        time_str = raw_ts[11:19] if len(raw_ts) >= 19 else raw_ts

        s_dur = sum(getattr(n, "duration_seconds", 0.0) for n in s_nodes)
        s_bq_dur = sum(getattr(n, "bq_duration_seconds", 0.0) or 0.0 for n in s_nodes)
        s_bytes = sum(getattr(n, "total_bytes_processed", 0) or 0 for n in s_nodes)
        s_billed = sum(getattr(n, "total_bytes_billed", 0) or 0 for n in s_nodes)
        s_slots = sum(getattr(n, "slot_millis", 0) or 0 for n in s_nodes)

        run_lines = [
            f"### {s_badge} Run #{run_num}: {time_str} ({len(s_nodes)} node{'s' if len(s_nodes) != 1 else ''})",
            f"* **Status:** {s_status}",
            f"* **Full Runtime:** `{format_duration(s_dur)}`",
        ]
        if s_bq_dur > 0:
            run_lines.append(f"* **BigQuery Cloud Execution Duration:** `{format_duration(s_bq_dur)}`")
        if s_bytes > 0:
            run_lines.append(f"* **Bytes Processed:** `{format_bytes(s_bytes)}`")
            run_lines.append(f"* **Bytes Billed:** `{format_bytes(s_billed)}`")
        if s_slots > 0:
            run_lines.append(f"* **Slot Millis:** `{s_slots:,} ms`")

        run_lines.append("* **Nodes:**")
        for n in s_nodes:
            n_status_icon = "✅" if getattr(n, "status", "") == "SUCCESS" else "❌"
            n_dur = format_duration(getattr(n, "duration_seconds", 0.0))
            run_lines.append(f"  * {n_status_icon} `{n.node_name}` ({n_dur})")

        run_lines.append("\n&nbsp;")

        doc.append("\n".join(run_lines))

    return "\n\n".join(doc)
