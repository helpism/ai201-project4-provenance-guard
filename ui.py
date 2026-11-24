"""Provenance Guard — Gradio Web Interface
=========================================
Standalone UI client that communicates with the local Flask backend
(app.py) over HTTP so all submissions, appeals, and analytics persist
to audit_log.db.
"""

import gradio as gr
import requests

API_BASE = "http://127.0.0.1:5000"


def _safe_json(resp):
    """Parse JSON safely, surfacing raw text if the server returned non-JSON."""
    if not resp.text or not resp.text.strip():
        return {"error": f"Empty response from server (HTTP {resp.status_code})"}
    try:
        return resp.json()
    except ValueError:
        return {"error": f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"}


def submit_text(creator_id: str, text: str):
    """Send text to POST /submit and format the response for the UI."""
    if not creator_id.strip() or not text.strip():
        return "⚠️ Please enter both a Creator ID and text to analyze.", "", {}

    try:
        resp = requests.post(
            f"{API_BASE}/submit",
            json={"creator_id": creator_id.strip(), "text": text},
            timeout=30,
        )
        data = _safe_json(resp)
        if resp.status_code != 201:
            return f"❌ Error ({resp.status_code}): {data.get('error', 'Unknown error')}", "", {}

        scores = data.get("signal_scores", {})
        breakdown = {
            "Combined Confidence": round(data["confidence"], 4),
            "Signal 1 — LLM (65%)": round(scores.get("llm", 0.0), 4),
            "Signal 2 — Stylometric (20%)": round(scores.get("stylometric", 0.0), 4),
            "Signal 3 — Entropy (15%)": round(scores.get("entropy", 0.0), 4),
        }
        return data["attribution"], data["content_id"], breakdown

    except requests.exceptions.ConnectionError:
        return "❌ Cannot connect to Flask backend. Make sure `python app.py` is running.", "", {}
    except Exception as exc:
        return f"❌ Unexpected error: {exc}", "", {}


def submit_appeal(content_id: str, reasoning: str):
    """Send an appeal to POST /appeal."""
    if not content_id.strip() or not reasoning.strip():
        return "⚠️ Both Content ID and Appeal Reasoning are required."

    try:
        resp = requests.post(
            f"{API_BASE}/appeal",
            json={
                "content_id": content_id.strip(),
                "creator_reasoning": reasoning.strip(),
            },
            timeout=15,
        )
        data = _safe_json(resp)
        if resp.status_code != 200:
            return f"❌ Error ({resp.status_code}): {data.get('error', 'Unknown error')}"

        return f"✅ {data['message']} (Status: {data['status']})"

    except requests.exceptions.ConnectionError:
        return "❌ Cannot connect to Flask backend. Make sure `python app.py` is running."
    except Exception as exc:
        return f"❌ Unexpected error: {exc}"


def refresh_dashboard():
    """Fetch system metrics from GET /analytics and recent rows from GET /log."""
    try:
        analytics_resp = _safe_json(requests.get(f"{API_BASE}/analytics", timeout=10))
        if "error" in analytics_resp:
            return f"❌ Error loading analytics: {analytics_resp['error']}", []

        log_resp = _safe_json(requests.get(f"{API_BASE}/log?limit=20", timeout=10))
        if isinstance(log_resp, dict) and "error" in log_resp:
            return f"❌ Error loading log: {log_resp['error']}", []

        metrics_md = (
            f"### System Metrics\n"
            f"- **Total Submissions:** {analytics_resp.get('total_submissions', 0)}\n"
            f"- **Appeal Rate:** {analytics_resp.get('appeal_rate_percent', 0.0)}%\n"
            f"- **Average AI Confidence:** {analytics_resp.get('average_confidence', 0.0)}"
        )

        rows = [
            [
                row.get("timestamp", ""),
                row.get("content_id", ""),
                row.get("creator_id", ""),
                round(row.get("confidence", 0.0), 4),
                row.get("status", ""),
                row.get("appeal_reasoning") or "",
            ]
            for row in log_resp
        ]
        return metrics_md, rows

    except requests.exceptions.ConnectionError:
        return "❌ Cannot connect to Flask backend. Make sure `python app.py` is running.", []
    except Exception as exc:
        return f"❌ Error loading dashboard: {exc}", []


# ---------------------------------------------------------------------------
# Gradio Layout
# ---------------------------------------------------------------------------
with gr.Blocks(title="Provenance Guard", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        "# 🛡️ Provenance Guard\n"
        "Multi-signal AI text detection, transparency attribution, and creator appeals."
    )

    with gr.Tabs():
        # --- Tab 1: Analyze Text ------------------------------------------
        with gr.Tab("1. Analyze Text"):
            with gr.Row():
                with gr.Column(scale=2):
                    creator_input = gr.Textbox(
                        label="Creator ID",
                        placeholder="e.g., creator-42",
                        value="demo-user-01",
                    )
                    text_input = gr.Textbox(
                        label="Text to Evaluate",
                        placeholder="Paste prose here to evaluate human vs. AI provenance...",
                        lines=8,
                    )
                    analyze_btn = gr.Button("Analyze Provenance", variant="primary")

                with gr.Column(scale=1):
                    attribution_out = gr.Textbox(
                        label="Transparency Label",
                        lines=4,
                        interactive=False,
                    )
                    content_id_out = gr.Textbox(
                        label="Content ID (Copy for Appeals)",
                        interactive=False,
                    )
                    scores_out = gr.JSON(label="Signal Breakdown (0.0 = Human, 1.0 = AI)")

            analyze_btn.click(
                fn=submit_text,
                inputs=[creator_input, text_input],
                outputs=[attribution_out, content_id_out, scores_out],
            )

        # --- Tab 2: Submit Appeal -----------------------------------------
        with gr.Tab("2. Creator Appeal"):
            gr.Markdown("Contest a classification by submitting the `Content ID` from Tab 1.")
            appeal_id_input = gr.Textbox(
                label="Content ID (UUID)",
                placeholder="Paste content_id here...",
            )
            appeal_reason_input = gr.Textbox(
                label="Creator Reasoning",
                placeholder="Explain why this work was authored by a human...",
                lines=4,
            )
            appeal_btn = gr.Button("Submit Appeal", variant="primary")
            appeal_status_out = gr.Textbox(label="Appeal Status", interactive=False)

            content_id_out.change(
                fn=lambda cid: cid,
                inputs=[content_id_out],
                outputs=[appeal_id_input],
            )

            appeal_btn.click(
                fn=submit_appeal,
                inputs=[appeal_id_input, appeal_reason_input],
                outputs=[appeal_status_out],
            )

        # --- Tab 3: Audit Log & Analytics ---------------------------------
        with gr.Tab("3. Audit Log & Analytics"):
            refresh_btn = gr.Button("Refresh Metrics & Log")
            analytics_md_out = gr.Markdown("Click **Refresh Metrics & Log** to load data.")
            log_table_out = gr.Dataframe(
                headers=["Timestamp", "Content ID", "Creator ID", "Confidence", "Status", "Appeal Reasoning"],
                datatype=["str", "str", "str", "number", "str", "str"],
                interactive=False,
                wrap=True,
            )

            refresh_btn.click(
                fn=refresh_dashboard,
                inputs=[],
                outputs=[analytics_md_out, log_table_out],
            )

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)