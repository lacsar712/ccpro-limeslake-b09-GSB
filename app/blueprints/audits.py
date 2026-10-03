from datetime import timedelta

from flask import Blueprint, render_template
from flask_login import login_required

from app.models import PeakAuditLog, utcnow

bp = Blueprint("audits", __name__, url_prefix="/audits")


@bp.route("/peaks")
@login_required
def peak_audit():
    since = utcnow() - timedelta(days=7)
    logs = (
        PeakAuditLog.query.filter(PeakAuditLog.changed_at >= since)
        .order_by(PeakAuditLog.changed_at.desc())
        .all()
    )
    return render_template("audits/peaks.html", logs=logs, since=since)
