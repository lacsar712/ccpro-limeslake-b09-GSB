from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Pond, SlakeBatch
from app.services.peaks import PeakWriteError, apply_peak_write

bp = Blueprint("batches", __name__, url_prefix="/batches")


@bp.route("/")
@login_required
def list_batches():
    batches = (
        SlakeBatch.query.join(Pond)
        .order_by(SlakeBatch.started_at.desc())
        .all()
    )
    return render_template("batches/list.html", batches=batches)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_batch():
    ponds = Pond.query.order_by(Pond.code).all()
    if request.method == "POST":
        pond_id = int(request.form["pond_id"])
        started_raw = request.form.get("started_at") or ""
        target = float(request.form.get("target_temp_c") or 80)
        peak_raw = (request.form.get("peak_temp_c") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        started_at = (
            datetime.fromisoformat(started_raw)
            if started_raw
            else datetime.utcnow()
        )
        batch = SlakeBatch(
            pond_id=pond_id,
            started_at=started_at,
            target_temp_c=target,
            peak_temp_c=None,
            notes=notes,
        )
        db.session.add(batch)
        try:
            db.session.flush()
            # 建单时顺带填的峰值同样走统一入口：熟化中 + 首填 + 审计。
            if peak_raw:
                apply_peak_write(
                    pond_id=pond_id,
                    batch_id=batch.id,
                    user=current_user,
                    raw_value=peak_raw,
                    expected_version=0,
                )
            db.session.commit()
            flash("熟化批次已登记", "ok")
        except PeakWriteError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return render_template("batches/form.html", ponds=ponds, batch=None)
        pond = db.session.get(Pond, pond_id)
        return redirect(
            url_for(
                "board.floor_plan",
                plant_id=pond.plant_id if pond else None,
                pond=pond_id,
            )
        )
    return render_template("batches/form.html", ponds=ponds, batch=None)


@bp.route("/<int:batch_id>/edit", methods=["GET", "POST"])
@login_required
def edit_batch(batch_id: int):
    batch = SlakeBatch.query.get_or_404(batch_id)
    ponds = Pond.query.order_by(Pond.code).all()
    if request.method == "POST":
        pond_id = int(request.form["pond_id"])
        started_raw = request.form.get("started_at") or ""
        peak_raw = (request.form.get("peak_temp_c") or "").strip()
        peak_present = "peak_temp_c" in request.form
        expected_version = request.form.get("peak_version", type=int)
        notes = (request.form.get("notes") or "").strip()

        try:
            # 峰值写入与抽屉共用同一套分界/并发/审计规则，不存在旁路。
            if peak_present and peak_raw:
                apply_peak_write(
                    pond_id=batch.pond_id,
                    batch_id=batch.id,
                    user=current_user,
                    raw_value=peak_raw,
                    expected_version=expected_version,
                )
            elif peak_present and not peak_raw and batch.peak_temp_c is not None:
                # 不允许借“清空输入框”抹掉已有峰值（同样属于改写）。
                raise PeakWriteError("已有峰值不能清空，修正请直接填写新值")

            batch.pond_id = pond_id
            if started_raw:
                batch.started_at = datetime.fromisoformat(started_raw)
            batch.target_temp_c = float(request.form.get("target_temp_c") or 80)
            batch.notes = notes
            db.session.commit()
            flash("熟化批次已更新", "ok")
            return redirect(
                url_for(
                    "board.floor_plan",
                    plant_id=batch.pond.plant_id,
                    pond=batch.pond_id,
                )
            )
        except PeakWriteError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            batch = SlakeBatch.query.get_or_404(batch_id)
    return render_template("batches/form.html", ponds=ponds, batch=batch)
