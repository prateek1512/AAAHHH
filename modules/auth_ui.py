"""Shared Streamlit login and logout presentation for both portals."""

from __future__ import annotations

import streamlit as st

from modules.auth import (
    SESSION_TOKEN_KEY,
    AuthService,
    AuthenticationError,
    AuthorizationError,
    ApplicationSummary,
    NotificationSummary,
    Role,
)
from modules.database import create_database, initialize_database


@st.cache_resource
def get_auth_service() -> AuthService:
    initialize_database()
    return AuthService(create_database())


def render_login(portal_role: Role, title: str) -> str | None:
    """Render the shared login form and return a valid session token, if signed in."""
    st.title(title)
    service = get_auth_service()
    token = st.session_state.get(SESSION_TOKEN_KEY)

    if isinstance(token, str):
        try:
            principal = service.require_role(token, portal_role)
            st.sidebar.write(f"Signed in as **{principal.full_name}**")
            if st.sidebar.button("Log out", key=f"{portal_role}_logout"):
                service.logout(token)
                st.session_state.pop(SESSION_TOKEN_KEY, None)
                st.rerun()
            return token
        except (AuthenticationError, AuthorizationError):
            service.logout(token)
            st.session_state.pop(SESSION_TOKEN_KEY, None)
            st.error("Your session is invalid or does not have access to this portal. Sign in again.")

    with st.form(f"{portal_role}_login_form"):
        email = st.text_input("Email", autocomplete="email")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        try:
            token = service.login(email, password, expected_role=portal_role)
        except AuthenticationError:
            st.error("Invalid email or password.")
        else:
            st.session_state[SESSION_TOKEN_KEY] = token
            st.rerun()
    return None


def render_application_summaries(applications: tuple[ApplicationSummary, ...]) -> None:
    if not applications:
        st.info("No applications to display.")
        return
    for application in applications:
        st.write(f"**{application.application_number}** — {application.status}")


def render_notification_summaries(notifications: tuple[NotificationSummary, ...]) -> None:
    if not notifications:
        st.info("No notifications.")
        return
    for notification in notifications:
        st.write(f"**{notification.title}** — {notification.message}")
