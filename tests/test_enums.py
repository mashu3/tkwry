"""Tests for public enum exports."""

from __future__ import annotations

from tkwry import DragDropEvent, NewWindowResponse, PageLoadEvent


def test_page_load_event_members() -> None:
    assert PageLoadEvent.Started != PageLoadEvent.Finished
    assert PageLoadEvent.Started == PageLoadEvent.Started


def test_drag_drop_event_members() -> None:
    assert DragDropEvent.Enter != DragDropEvent.Drop
    assert DragDropEvent.Leave != DragDropEvent.Over


def test_new_window_response_members() -> None:
    assert NewWindowResponse.Allow != NewWindowResponse.Deny
