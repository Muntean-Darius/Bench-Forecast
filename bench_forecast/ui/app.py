"""Bench Forecast — Human-in-the-Loop Allocation Console (Streamlit UI).

Single-screen dashboard that:
1. Shows open demands filtered by win probability
2. Shows the RAG-matched best candidate
3. Lets managers Generate a forecast (triggers LLM pipeline via API)
4. Presents LLM recommendations with confidence score and reasoning
5. Provides Approve / Reject-with-feedback HITL buttons
6. On rejection+feedback: triggers LLM revision and displays revised plan
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
import streamlit as st

try:
    from src.database.vector_store import VectorStoreManager
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.database.vector_store import VectorStoreManager

st.set_page_config(
    page_title="Bench Forecast | Allocation Console",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE_URL = "http://127.0.0.1:8000"

# Ollama is slow — Llama-2 on CPU can take 4-6 min for 16 LLM calls.
# This timeout must be longer than the full pipeline duration.
GENERATE_TIMEOUT_SEC = 600   # 10 min — covers slow Ollama runs
EXECUTE_TIMEOUT_SEC = 120    # 2 min — revision planning


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def load_mock_data() -> Dict[str, Any]:
    data_path = Path(__file__).resolve().parents[1] / "data" / "mock_data.json"
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"demands": [], "employees": []}


def call_generate_forecast(
    department_id: str = "ALL",
    horizon_days: int = 90,
    min_win_probability: float = 0.75,
) -> Optional[Dict[str, Any]]:
    """POST /generate — triggers the full LLM pipeline."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/generate",
            json={
                "department_id": department_id,
                "horizon_days": int(horizon_days),
                "min_win_probability": float(min_win_probability),
            },
            timeout=GENERATE_TIMEOUT_SEC,
        )
        if response.status_code in (200, 202):
            return response.json()
        st.error(f"Generate failed (HTTP {response.status_code}): {response.text[:300]}")
    except requests.exceptions.Timeout:
        st.error(
            f"⏱️ Request timed out after {GENERATE_TIMEOUT_SEC}s. "
            "The LLM pipeline is still running on the server — "
            "try refreshing in a few minutes or switch to Groq for faster inference."
        )
    except requests.exceptions.ConnectionError:
        st.error("❌ Cannot connect to API. Is `python server.py` running?")
    except Exception as e:
        st.error(f"Generate error: {e}")
    return None


def call_execute_forecast(
    recommendation_id: str,
    thread_id: str,
    approved: bool,
    rejection_feedback: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """POST /execute — approve or reject with optional feedback for revision."""
    try:
        payload: Dict[str, Any] = {
            "recommendation_id": recommendation_id,
            "thread_id": thread_id,
            "approved": approved,
            "approver_name": st.session_state.get("approver_name", "Manager"),
        }
        if not approved and rejection_feedback:
            payload["rejection_feedback"] = rejection_feedback

        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/execute",
            json=payload,
            timeout=EXECUTE_TIMEOUT_SEC,
        )
        if response.status_code == 200:
            return response.json()
        st.error(f"Execute failed (HTTP {response.status_code}): {response.text[:300]}")
    except requests.exceptions.Timeout:
        st.error(f"⏱️ Execute timed out after {EXECUTE_TIMEOUT_SEC}s.")
    except requests.exceptions.ConnectionError:
        st.error("❌ Cannot connect to API.")
    except Exception as e:
        st.error(f"Execute error: {e}")
    return None


def call_submit_feedback(recommendation_id: str, feedback: str) -> Optional[Dict[str, Any]]:
    """POST /feedback — audit notes (no HITL decision)."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/feedback",
            json={"recommendation_id": recommendation_id, "feedback": feedback},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# UI sub-components
# ---------------------------------------------------------------------------

def render_demand_card(demand: Dict[str, Any]) -> None:
    """Left panel — selected demand details."""
    win = demand.get("win_probability", 0)
    win_color = "🟢" if win >= 0.85 else "🟡" if win >= 0.70 else "🔴"

    with st.container(border=True):
        st.markdown(f"#### {demand.get('role')}")
        c1, c2 = st.columns(2)
        c1.text(f"Project: {demand.get('project_id')}")
        c2.text(f"Start: {demand.get('start_date')}")
        c1.text(f"Headcount: {demand.get('headcount')}")
        c2.text(f"Win Prob: {win_color} {win:.0%}")
        st.progress(win)

        st.markdown("**Required Skills:**")
        skills_html = " ".join(
            f"`{s}`" for s in demand.get("required_skills", [])
        )
        st.markdown(skills_html)

        st.markdown("**Statement of Work:**")
        st.caption(demand.get("description", "No description provided."))


def render_candidate_card(
    candidate: Dict[str, Any],
    match_score_str: str,
) -> None:
    """Candidate summary pulled from ChromaDB similarity search."""
    with st.container(border=True):
        st.markdown(f"#### 👤 {candidate.get('name')}")
        c1, c2, c3 = st.columns(3)
        c1.metric("RAG Match", match_score_str)
        c2.metric("Experience", f"{candidate.get('experience_years', '?')} yrs")
        cost = candidate.get("cost_rate")
        c3.metric("Cost Rate", f"€{cost}/hr" if cost else "N/A")

        proj = candidate.get("current_project", None)
        st.caption(f"Current project: {proj or 'On bench ✅'}")
        st.caption(f"Available from: {candidate.get('available_from', '?')}")

        st.markdown("**Skills:**")
        st.write(", ".join(f"`{s}`" for s in candidate.get("skills", [])))

        bio = candidate.get("bio") or candidate.get("profile_text", "")
        if bio:
            with st.expander("📄 Semantic Retrieval Context (RAG)"):
                st.write(bio)


def render_recommendations_panel(
    recommendations: Dict[str, Any],
    rec_id: str,
    thread_id: str,
    revision_count: int = 0,
) -> None:
    """Full LLM recommendation panel with HITL approve/reject flow."""

    if revision_count > 0:
        st.info(f"🔄 **Revised Proposal** — revision {revision_count}/2, incorporating your feedback")

    # Confidence bar
    confidence = recommendations.get("confidence_score", 0)
    conf_color = "🟢" if confidence >= 0.8 else "🟡" if confidence >= 0.6 else "🔴"
    st.progress(confidence, text=f"{conf_color} AI Confidence: {confidence:.0%}")

    if recommendations.get("revision_note"):
        st.caption(f"📝 Revision addressed: _{recommendations['revision_note'][:120]}_")

    # Reallocations table
    reallocations = recommendations.get("reallocations", [])
    if reallocations:
        st.markdown("**🔀 Proposed Reallocations**")
        for r in reallocations:
            score = r.get("match_score", 0)
            score_str = f"{score:.0%}" if score else "—"
            c1, c2, c3 = st.columns([2, 3, 1])
            c1.markdown(f"`{r.get('employee_id', '?')}`")
            c2.markdown(
                f"→ `{r.get('target_project_id', '?')}` "
                f"*({r.get('role', 'Unspecified')})*"
            )
            c3.markdown(f"**{score_str}**")
    else:
        st.caption("No direct reallocations proposed.")

    # Trainings
    trainings = recommendations.get("trainings", [])
    if trainings:
        st.markdown("**📚 Training Proposals**")
        for t in trainings:
            skills = ", ".join(t.get("target_skills", []))
            st.markdown(
                f"• `{t.get('employee_id')}` → {skills} "
                f"({t.get('duration_weeks')}w)"
            )

    # Hirings
    hirings = recommendations.get("hirings", [])
    if hirings:
        st.markdown("**🔍 External Hires Needed**")
        for h in hirings:
            skills = ", ".join(h.get("required_skills", []))
            st.markdown(
                f"• **{h.get('role')}** × {h.get('headcount')} — [{skills}]"
            )

    # Reasoning
    st.markdown("**💬 Strategic Reasoning**")
    st.info(recommendations.get("reasoning", ""))

    st.divider()

    # ── HITL Decision ─────────────────────────────────────────────────────────
    st.markdown("##### ✋ Decision Required (Human-in-the-Loop)")

    decision_status = st.session_state.get(f"status_{rec_id}", "pending")

    if decision_status == "APPROVED":
        st.success("✅ Allocation **APPROVED** and committed to the database.")
        return
    if decision_status == "REJECTED_TERMINATED":
        st.error("❌ Proposal **rejected**. Workflow terminated — no changes made.")
        return
    if decision_status == "SUPERSEDED":
        st.info("🔄 This proposal was superseded by a revision. See the updated plan above.")
        return

    col_approve, col_reject = st.columns(2)

    with col_approve:
        if st.button(
            "✅ Approve Allocation",
            type="primary",
            use_container_width=True,
            key=f"approve_{rec_id}",
        ):
            with st.spinner("Committing to database..."):
                result = call_execute_forecast(rec_id, thread_id, approved=True)
            if result and result.get("status") == "completed":
                exec_s = result.get("execution_status", "")
                st.session_state[f"status_{rec_id}"] = "APPROVED"
                st.session_state["last_execution_status"] = exec_s
                st.rerun()
            else:
                st.error("Approval failed — check the server logs.")

    with col_reject:
        if st.button(
            "❌ Reject Proposal",
            use_container_width=True,
            key=f"reject_{rec_id}",
        ):
            st.session_state["show_feedback_for"] = rec_id

    # Rejection feedback panel
    if st.session_state.get("show_feedback_for") == rec_id:
        st.markdown("---")
        st.markdown("**Provide feedback to improve the proposal:**")
        feedback_text = st.text_area(
            "Rejection reason & revision guidance",
            placeholder=(
                "e.g. 'EMP001 is already committed elsewhere until November. "
                "Please propose David Patel or an external hire instead. "
                "Also prioritise candidates with Kubernetes experience.'"
            ),
            key=f"feedback_input_{rec_id}",
            height=110,
        )

        fb1, fb2 = st.columns(2)

        with fb1:
            if st.button(
                "🔄 Reject & Request Revision",
                type="primary",
                use_container_width=True,
                key=f"submit_revision_{rec_id}",
            ):
                if not feedback_text.strip():
                    st.warning("Please enter feedback before requesting a revision.")
                else:
                    with st.spinner(
                        "Sending feedback — LLM generating revised plan "
                        "(may take ~1-3 min)..."
                    ):
                        result = call_execute_forecast(
                            rec_id, thread_id,
                            approved=False,
                            rejection_feedback=feedback_text.strip(),
                        )
                    if result and result.get("status") == "revised_for_review":
                        new_rec_id = result.get("recommendation_id", rec_id)
                        st.session_state["active_rec_id"] = new_rec_id
                        st.session_state["active_thread_id"] = result.get("thread_id", thread_id)
                        st.session_state["api_forecast_result"] = result
                        st.session_state["show_feedback_for"] = None
                        st.session_state[f"status_{rec_id}"] = "SUPERSEDED"
                        st.toast("✅ Revised proposal ready for review!")
                        st.rerun()
                    else:
                        st.error("Revision failed or API returned unexpected response.")

        with fb2:
            if st.button(
                "🚫 Reject & Terminate",
                use_container_width=True,
                key=f"terminate_{rec_id}",
            ):
                with st.spinner("Terminating workflow..."):
                    result = call_execute_forecast(rec_id, thread_id, approved=False)
                if result:
                    st.session_state[f"status_{rec_id}"] = "REJECTED_TERMINATED"
                    st.session_state["show_feedback_for"] = None
                    st.rerun()


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------

def render_dashboard() -> None:
    st.title("🏢 Bench Forecast")
    st.caption(
        "RAG-powered workforce allocation with Human-in-the-Loop approval and feedback-driven revision."
    )

    data = load_mock_data()
    demands: List[Dict[str, Any]] = data.get("demands", [])
    employees: List[Dict[str, Any]] = data.get("employees", [])

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ Configuration")

        # API health
        backend_online = False
        try:
            res = requests.get(f"{API_BASE_URL}/api/v1/health", timeout=2)
            if res.status_code == 200:
                backend_online = True
                health = res.json()
        except Exception:
            pass

        if backend_online:
            st.success("🟢 API Connected")
            st.caption(
                f"LLM: `{health.get('llm_provider', '?')}` | "
                f"Active workflows: {health.get('active_workflows', 0)}"
            )
        else:
            st.warning("🔴 API Offline — start with `python server.py`")

        st.session_state["approver_name"] = st.text_input(
            "Reviewer Name",
            value=st.session_state.get("approver_name", "Manager"),
        )

        st.divider()
        st.subheader("🔭 Forecast Parameters")

        # horizon slider: integer days, easy to read
        horizon_days = st.slider(
            "Forecast Horizon (days)",
            min_value=30,
            max_value=180,
            value=st.session_state.get("horizon_days_val", 90),
            step=15,
            help="Include employees whose available_from falls within this window.",
        )
        st.session_state["horizon_days_val"] = horizon_days

        # win probability slider: integer 50–100 → divide by 100 when sending
        win_prob_pct = st.slider(
            "Min Win Probability",
            min_value=50,
            max_value=100,
            value=st.session_state.get("win_prob_pct_val", 75),
            step=5,
            format="%d%%",
            help="Only match against pipeline opportunities above this confidence level.",
        )
        st.session_state["win_prob_pct_val"] = win_prob_pct
        min_win_prob = win_prob_pct / 100.0

        st.caption(
            f"📊 Supply: **{len(employees)}** profiles | "
            f"Demands: **{len(demands)}** | "
            f"Horizon: **{horizon_days}d** | "
            f"Win≥**{win_prob_pct}%**"
        )

        st.divider()
        st.subheader("📋 Demand Selection")

        # Filter demands by win probability client-side for the dropdown
        filtered_demands = [
            d for d in demands if d.get("win_probability", 0) >= min_win_prob
        ]
        demand_options = {
            f"{d['id']} — {d['role']} ({d.get('win_probability', 0):.0%})": d
            for d in filtered_demands
        }

        if not demand_options:
            st.warning("No demands meet the current win probability filter.")
            selected_demand = None
        else:
            selected_key = st.selectbox(
                "Open Role Pipeline:",
                list(demand_options.keys()),
            )
            selected_demand = demand_options.get(selected_key)

        st.divider()

        generate_disabled = not backend_online
        if st.button(
            "🚀 Generate Forecast",
            type="primary",
            use_container_width=True,
            disabled=generate_disabled,
            help="Runs the full AI pipeline: data extraction → RAG matching → planning.",
        ):
            est_min = "1-2" if "groq" in (
                health.get("llm_provider", "") if backend_online else ""
            ) else "3-6"
            with st.spinner(
                f"Running LLM pipeline (~{est_min} min): "
                "data extraction → RAG matching → planning…"
            ):
                resp = call_generate_forecast("ALL", horizon_days, min_win_prob)

            if resp:
                st.session_state["api_forecast_result"] = resp
                st.session_state["active_thread_id"] = resp.get("thread_id", "")
                st.session_state["active_rec_id"] = resp.get("recommendation_id", "")
                st.session_state["show_feedback_for"] = None
                horizon_used = resp.get("horizon_days", horizon_days)
                win_used = resp.get("min_win_probability", min_win_prob)
                st.toast(
                    f"✅ Forecast ready — {horizon_used}d horizon, "
                    f"win≥{win_used:.0%}"
                )

        if generate_disabled:
            st.caption("Start the API server to enable forecast generation.")

        # Show last execution status if available
        last_exec = st.session_state.get("last_execution_status")
        if last_exec:
            st.success(f"Last execution: `{last_exec}`")

    # ── Main 2-column layout ─────────────────────────────────────────────────
    col_demand, col_review = st.columns([1, 1], gap="large")

    # Left — Role Specification
    with col_demand:
        st.subheader("📌 Role Specification")
        if selected_demand:
            render_demand_card(selected_demand)
        else:
            st.info("Select a demand from the sidebar.")

    # Right — Allocation Recommendation
    with col_review:
        st.subheader("🤖 Proposed Allocation")

        # Always show the RAG-matched candidate (independent of LLM pipeline)
        matched_candidate: Optional[Dict[str, Any]] = None
        match_score_str = "—"

        if selected_demand:
            try:
                vector_mgr = VectorStoreManager()
                query = (
                    f"{selected_demand.get('role', '')} "
                    f"{selected_demand.get('description', '')}"
                )
                rag_results = vector_mgr.similarity_search(query, k=1)
                if rag_results:
                    top = rag_results[0]
                    matched_candidate = next(
                        (e for e in employees if e["id"] == top["id"]), None
                    )
                    match_score_str = f"{top['similarity_score']:.0%}"
            except Exception:
                pass

        if not matched_candidate and employees:
            matched_candidate = employees[0]

        if matched_candidate:
            render_candidate_card(matched_candidate, match_score_str)

        st.divider()

        # LLM Recommendation panel
        api_result = st.session_state.get("api_forecast_result")
        rec_id = st.session_state.get("active_rec_id", "")
        thread_id = st.session_state.get("active_thread_id", "")
        revision_count = 0

        if api_result:
            recommendations = api_result.get("recommendations") or {}
            revision_count = api_result.get("revision_count", 0)
            render_recommendations_panel(
                recommendations, rec_id, thread_id, revision_count
            )
        else:
            st.info(
                "👆 Click **Generate Forecast** in the sidebar to run the AI pipeline.\n\n"
                "The API will:\n"
                "1. Fetch bench employees available within your horizon\n"
                "2. Query ChromaDB for semantic matches (RAG)\n"
                "3. Run the LLM skill matcher for each pair\n"
                "4. Generate an allocation plan\n"
                "5. Pause here for your review"
            )

        # Audit Notes expander
        with st.expander("📝 Manager Audit Notes"):
            note_text = st.text_area(
                "Observations:",
                placeholder="Enter review rationale, caveats, or adjustments…",
                key="audit_note_text",
            )
            if st.button("💾 Save Note", key="save_audit_note"):
                if rec_id and note_text.strip():
                    call_submit_feedback(rec_id, note_text.strip())
                    st.success("Note recorded.")
                elif not rec_id:
                    st.warning("No active recommendation — generate a forecast first.")
                else:
                    st.warning("Note is empty.")


if __name__ == "__main__":
    render_dashboard()
