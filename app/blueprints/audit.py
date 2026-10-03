from datetime import timedelta

from flask import Blueprint, render_template
from flask_login import login_required

from app.models import PeakAudit, utcnow

bp = Blueprint("audit", __name__, url_prefix="/audit")

ACTION_LABELS = {
    PeakAudit.ACTION_FIRST: "首填",
    PeakAudit.ACTION_EDIT: "修正",
}


@bp.route("/peak")
@login_required
def peak_audit():
    since = utcnow() - timedelta(days=7)
    records = (
        PeakAudit.query.filter(PeakAudit.changed_at >= since)
        .order_by(PeakAudit.changed_at.desc())
        .all()
    )
    return render_template(
        "audit/peak.html",
        records=records,
        action_labels=ACTION_LABELS,
        since=since,
    )
