import os

import requests
import streamlit as st


API_BASE = os.getenv("REVIEW_API_BASE", "http://api:8000/api/v1")
API_KEY = os.getenv("REVIEW_API_KEY", "")


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _get(path: str):
    response = requests.get(f"{API_BASE}{path}", headers=_headers(), timeout=10)
    response.raise_for_status()
    return response.json()


def _post(path: str, payload: dict):
    response = requests.post(f"{API_BASE}{path}", json=payload, headers=_headers(), timeout=10)
    response.raise_for_status()
    return response.json()


def _put(path: str, payload: dict):
    response = requests.put(f"{API_BASE}{path}", json=payload, headers=_headers(), timeout=10)
    response.raise_for_status()
    return response.json()


st.set_page_config(page_title="Document Review Queue", layout="wide")
st.title("Document Intelligence Review Queue")

field_tab, draft_tab = st.tabs(["Field Corrections", "Draft Review"])

with field_tab:
    queue_response = requests.get(f"{API_BASE}/reviews/queue", headers=_headers(), timeout=10)
    queue_response.raise_for_status()
    tasks = queue_response.json()

    if not tasks:
        st.info("No pending review tasks.")
    else:
        selected = st.selectbox(
            "Pending tasks",
            options=tasks,
            format_func=lambda item: f"{item['document_id']} :: {item['field_name']} :: {item['confidence']}",
        )
        st.subheader("Task Details")
        st.json(selected)
        corrected = st.text_input("Corrected value", value=str(selected["proposed_value"].get("value", "")))
        reviewer_name = st.text_input("Reviewer name", value="analyst")
        comment = st.text_area("Comment")
        if st.button("Submit correction"):
            payload = {
                "reviewer_name": reviewer_name,
                "corrected_value": {"value": corrected},
                "comment": comment or None,
            }
            response = requests.post(
                f"{API_BASE}/reviews/{selected['id']}/decision",
                json=payload,
                headers=_headers(),
                timeout=10,
            )
            if response.ok:
                st.success("Correction submitted.")
            else:
                st.error(response.text)

with draft_tab:
    docs = _get("/documents?limit=100").get("items", [])
    if not docs:
        st.info("No documents available.")
    else:
        doc = st.selectbox(
            "Document",
            options=docs,
            format_func=lambda item: f"{item['filename']} :: {item['status']} :: {item.get('document_type') or 'unknown'}",
        )
        draft_type = st.selectbox(
            "Draft type",
            options=[
                "internal_memo",
                "case_fact_summary",
                "contract_summary",
                "notice_summary",
                "document_checklist",
            ],
        )
        if st.button("Generate draft"):
            _post(f"/documents/{doc['id']}/drafts", {"draft_type": draft_type})
            st.success("Draft generation queued.")

        drafts = _get(f"/documents/{doc['id']}/drafts")
        if not drafts:
            st.info("No drafts for this document yet.")
        else:
            draft = st.selectbox(
                "Draft",
                options=drafts,
                format_func=lambda item: f"{item['draft_type']} :: {item['status']} :: {item['id'][:8]}",
            )
            reviewer = st.text_input("Draft reviewer name", value="analyst")
            st.caption(f"Model: {draft.get('model_id') or 'pending'}")
            for section in draft.get("content", {}).get("sections", []):
                with st.expander(section.get("title", section.get("key", "Section")), expanded=True):
                    edited = st.text_area(
                        "Content",
                        value=section.get("content", ""),
                        key=f"{draft['id']}:{section.get('key')}",
                    )
                    st.caption(f"Confidence: {section.get('confidence', 'low')}")
                    if section.get("evidence_chunk_ids"):
                        st.write("Evidence chunks:", ", ".join(section["evidence_chunk_ids"]))
                    if st.button("Submit section edit", key=f"submit:{draft['id']}:{section.get('key')}"):
                        _put(
                            f"/documents/{doc['id']}/drafts/{draft['id']}",
                            {
                                "reviewer_name": reviewer,
                                "sections": [
                                    {
                                        "key": section.get("key"),
                                        "edited_content": edited,
                                    }
                                ],
                            },
                        )
                        st.success("Draft edit submitted.")
