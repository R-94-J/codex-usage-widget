"""Sanitized count-only diagnostics; no prompts, responses, titles or keys."""
from .presentation import select_session,summarize_cost
from .usage import sum_usage


def snapshot_payload(snapshot,book,pinned_id=None):
    session=select_session(snapshot,pinned_id)
    current=summarize_cost(session.events if session else (),book)
    today=summarize_cost(snapshot.today_events,book)
    return {'updated_at':snapshot.updated_at.isoformat(),'selected_id':session.id if session else None,
            'selection_mode':'pinned' if pinned_id else 'latest_active_main_session',
            'model':session.model if session else None,
            'session_tokens':session.total_tokens if session else 0,
            'session_usage':sum_usage(session.events if session else ()),
            'today_tokens':snapshot.today_tokens,'today_usage':sum_usage(snapshot.today_events),
            'session_cost':current.display,'session_cost_label':current.label,
            'today_cost':today.display,'today_unpriced_events':today.unpriced_events,
            'session_unpriced_events':current.unpriced_events,
            'scope':'local session logs only; not account billing',
            'timezone':'UTC+08:00','files':snapshot.files_scanned,'session_count':len(snapshot.sessions),
            'errors':list(snapshot.errors)}
