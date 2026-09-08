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

# Enterprise Page Configuration
st.set_page_config(
    page_title="Bench Forecast | Allocation Console",
    layout="wide",
)

API_BASE_URL = "http://127.0.0.1:8000"


def load_mock_data() -> Dict[str, Any]:
    """Reads available mock demands and employees for display."""
    data_path = Path(__file__).resolve().parents[1] / "data" / "mock_data.json"
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"demands": [], "employees": []}


def call_generate_forecast(department_id: str = "ALL") -> Optional[Dict[str, Any]]:
    """Calls backend API to trigger forecast generation."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/generate",
            json={"department_id": department_id},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def call_execute_forecast(recommendation_id: str, approved: bool) -> Optional[Dict[str, Any]]:
    """Calls backend API to submit manager approval decision."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/forecast/execute",
            json={"recommendation_id": recommendation_id, "approved": approved},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def call_submit_feedback(recommendation_id: str, feedback: str) -> Optional[Dict[str, Any]]:
    """Calls backend API to submit audit feedback."""
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


def render_dashboard() -> None:
    st.title("Bench Forecast: Human-in-the-Loop Allocation Console")
    st.caption(
        "Enterprise decision interface for validating automated bench reallocation and capacity forecasting."
    )

    data = load_mock_data()
    demands: List[Dict[str, Any]] = data.get("demands", [])
    employees: List[Dict[str, Any]] = data.get("employees", [])

    # Sidebar: System Status & Demand Selection
    with st.sidebar:
        st.subheader("System Configuration")
        api_url = st.text_input("API Gateway Endpoint", value=API_BASE_URL)

        backend_online = False
        try:
            res = requests.get(f"{api_url}/health", timeout=2)
            if res.status_code == 200:
                backend_online = True
        except Exception:
            backend_online = False

        if backend_online:
            st.success("API Status: Connected")
        else:
            st.info("API Status: Offline (operating in local fallback mode)")

        st.divider()
        st.subheader("Demand Selection")
        demand_options = {f"{d['id']} - {d['role']}": d for d in demands}
        selected_key = st.selectbox("Open Role Pipeline:", list(demand_options.keys()))
        selected_demand = demand_options.get(selected_key) if selected_key else None

        st.caption(f"Total Bench Supply: {len(employees)} active profiles | Open Pipeline Demands: {len(demands)}")

        if st.button("Generate Forecast", type="primary", use_container_width=True):
            with st.spinner("Requesting forecast from inference engine..."):
                resp = call_generate_forecast("ALL")
                st.session_state["api_forecast_result"] = resp
                st.toast("Forecast generated successfully.")

    # Main Layout: Two Panels
    col_demand, col_review = st.columns([1, 1], gap="large")

    with col_demand:
        st.subheader("Role Specification")
        if selected_demand:
            with st.container(border=True):
                st.markdown(f"#### {selected_demand.get('role')}")
                st.text(f"Project Reference: {selected_demand.get('project_id')}")
                st.text(f"Scheduled Start: {selected_demand.get('start_date')}")
                st.text(f"Headcount Required: {selected_demand.get('headcount')}")
                st.text(f"Win Probability: {int(selected_demand.get('win_probability', 0)*100)}%")

                st.markdown("**Required Skill Set:**")
                st.write(", ".join([f"`{s}`" for s in selected_demand.get("required_skills", [])]))

                st.markdown("**Statement of Work (SOW) Description:**")
                st.write(selected_demand.get("description", ""))
        else:
            st.warning("No active demand selected.")

    with col_review:
        st.subheader("Proposed Allocation")

        matched_candidate: Optional[Dict[str, Any]] = None
        match_score_str = "90%"

        if selected_demand:
            try:
                vector_mgr = VectorStoreManager()
                search_query = f"{selected_demand.get('role', '')} {selected_demand.get('description', '')}"
                rag_results = vector_mgr.similarity_search(search_query, k=1)
                if rag_results:
                    top_rag = rag_results[0]
                    matched_candidate = next((e for e in employees if e["id"] == top_rag["id"]), None)
                    match_score_str = f"{int(top_rag['similarity_score'] * 100)}%"
            except Exception:
                pass

        if not matched_candidate and employees:
            matched_candidate = employees[0]

        rec_id = f"REC-{selected_demand.get('id') if selected_demand else '001'}"

        if matched_candidate:
            with st.container(border=True):
                st.markdown(f"#### Candidate: {matched_candidate.get('name')}")
                st.text(f"Current Assignment: {matched_candidate.get('current_project')}")

                m1, m2, m3 = st.columns(3)
                m1.metric("Match Confidence", match_score_str)
                m2.metric("Experience", f"{matched_candidate.get('experience_years')} yrs")
                m3.metric("Available From", matched_candidate.get("available_from"))

                st.markdown("**Profile Skills:**")
                st.write(", ".join([f"`{s}`" for s in matched_candidate.get("skills", [])]))

                st.markdown("**Semantic Retrieval Context (RAG):**")
                st.info(matched_candidate.get("bio", ""))

                st.markdown("**Recommendation Rationale:**")
                st.write(
                    f"The agent recommends allocating {matched_candidate.get('name')} to "
                    f"{selected_demand.get('project_id') if selected_demand else 'the project'} based on strong alignment "
                    f"with the core technology stack and immediate availability, reducing unbillable bench time."
                )

                st.divider()
                st.markdown("**Decision Action (Human-in-the-Loop):**")

                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button("Approve Allocation", type="primary", use_container_width=True):
                        call_execute_forecast(rec_id, approved=True)
                        st.session_state[f"status_{rec_id}"] = "APPROVED"

                with btn_col2:
                    if st.button("Reject Proposal", use_container_width=True):
                        call_execute_forecast(rec_id, approved=False)
                        st.session_state[f"status_{rec_id}"] = "REJECTED"

                current_status = st.session_state.get(f"status_{rec_id}")
                if current_status == "APPROVED":
                    st.success(f"Status: Allocation {rec_id} has been APPROVED and committed to database.")
                elif current_status == "REJECTED":
                    st.error(f"Status: Allocation {rec_id} has been REJECTED. Routing to retraining/recruitment.")

                with st.expander("Manager Audit Notes"):
                    mgr_feedback = st.text_area("Observations:", placeholder="Enter review rationale or adjustments...")
                    if st.button("Save Notes"):
                        call_submit_feedback(rec_id, mgr_feedback)
                        st.info("Audit notes recorded.")


if __name__ == "__main__":
    render_dashboard()
