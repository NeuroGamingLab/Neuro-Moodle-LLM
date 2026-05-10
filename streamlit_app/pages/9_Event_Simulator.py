"""Fire a Moodle event payload at /v1/events/moodle for testing the webhook."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.paths import default_course_id, event_secret_default
from lib.theme import apply_theme, banner

apply_theme("Event simulator")
api = NeuroAPI.from_env()

EVENTS = [
    r"\core\event\course_module_updated",
    r"\core\event\course_module_created",
    r"\core\event\course_module_deleted",
    r"\core\event\course_updated",
    r"\mod_assign\event\assignment_updated",
    r"\mod_resource\event\course_module_updated",
    r"\fake\event",
]

st.title("Moodle event simulator")
banner("Simulates Moodle's webhook payload. With a valid NEURO_EVENT_SECRET set on the API, an unknown event returns accepted=false; a watched event triggers re-ingest.")

with st.form("ev"):
    cols = st.columns(3)
    secret = cols[0].text_input("secret", value=event_secret_default(), type="password")
    eventname = cols[1].selectbox("eventname", EVENTS, index=0)
    course_id = cols[2].number_input("courseid", value=default_course_id(), step=1)
    cols2 = st.columns(2)
    objecttable = cols2[0].text_input("objecttable (optional)", value="")
    objectid = cols2[1].text_input("objectid (optional, int)", value="")
    go = st.form_submit_button("Send event", type="primary")

if go:
    body: dict = {"secret": secret, "eventname": eventname, "courseid": int(course_id)}
    if objecttable.strip():
        body["objecttable"] = objecttable.strip()
    if objectid.strip():
        try:
            body["objectid"] = int(objectid)
        except ValueError:
            st.error("objectid must be an integer or blank")
            st.stop()
    try:
        with st.spinner("Posting…"):
            res = api.event_post(body)
        st.success("Sent.")
        st.json(res)
    except APIError as exc:
        st.error(str(exc))
