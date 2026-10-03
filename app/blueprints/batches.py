from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Pond, SlakeBatch
from app.services.rules import RuleError, apply_peak_change

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
            if peak_raw:
                try:
                    new_peak = float(peak_raw)
                except ValueError:
                    raise RuleError("峰值温度格式无效")
                # 新建即首填：同样要求池处于熟化中，且必须落审计。
                db.session.flush()
                apply_peak_change(batch, new_peak, current_user)
            db.session.commit()
            flash("熟化批次已登记", "ok")
        except RuleError as exc:
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
    if request.method == "GET":
        batch = SlakeBatch.query.get_or_404(batch_id)
        ponds = Pond.query.order_by(Pond.code).all()
        return render_template("batches/form.html", ponds=ponds, batch=batch)

    # POST：峰值写入以批次当前所在池的状态为准，先锁池再锁批次（与作业板一致，避免死锁）。
    batch = SlakeBatch.query.get_or_404(batch_id)
    pond = db.session.get(Pond, batch.pond_id, with_for_update=True)
    batch = db.session.get(SlakeBatch, batch_id, with_for_update=True)
    ponds = Pond.query.order_by(Pond.code).all()

    new_pond_id = int(request.form["pond_id"])
    started_raw = request.form.get("started_at") or ""
    if started_raw:
        batch.started_at = datetime.fromisoformat(started_raw)
    batch.target_temp_c = float(request.form.get("target_temp_c") or 80)
    peak_raw = (request.form.get("peak_temp_c") or "").strip()
    batch.notes = (request.form.get("notes") or "").strip()
    try:
        if peak_raw:
            try:
                new_peak = float(peak_raw)
            except ValueError:
                raise RuleError("峰值温度格式无效")
            apply_peak_change(batch, new_peak, current_user)
        # 留空表示不改峰值；任何情况下都不能借表单清空已有峰值。
        batch.pond_id = new_pond_id
        db.session.commit()
        flash("熟化批次已更新", "ok")
        return redirect(
            url_for(
                "board.floor_plan",
                plant_id=batch.pond.plant_id,
                pond=batch.pond_id,
            )
        )
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        batch = SlakeBatch.query.get_or_404(batch_id)
        ponds = Pond.query.order_by(Pond.code).all()
    return render_template("batches/form.html", ponds=ponds, batch=batch)
