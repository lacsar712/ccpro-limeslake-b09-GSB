"""石灰熟化池业务规则。"""

from __future__ import annotations

from app.extensions import db
from app.models import PeakAuditLog, Pond, SlakeBatch, User

MIN_PEAK_TEMP_FOR_DRAWN = 60.0


class RuleError(ValueError):
    """业务规则校验失败。"""


def latest_batch_for_pond(pond: Pond) -> SlakeBatch | None:
    if not pond.batches:
        return None
    return max(pond.batches, key=lambda b: b.started_at)


def assert_can_write_peak(pond: Pond, old_value: float | None, user: User) -> None:
    """峰值温度写入分界：

    * 已出灰：全员只读；
    * 非熟化中（如注水中）：不可登记峰值；
    * 熟化中且峰值仍空：操作工可首填；
    * 熟化中但峰值已有：仅管理员可修正。
    """
    if pond.status == Pond.STATUS_DRAWN:
        raise RuleError("该池已出灰，峰值温度已锁定，任何人不得再修改")
    if pond.status != Pond.STATUS_SLAKING:
        raise RuleError("仅熟化中的批次可以登记峰值温度")
    if old_value is not None and not user.is_admin:
        raise RuleError("峰值温度已记录，操作工只能首次填写，不能修改已有数值")


def apply_peak_change(
    batch: SlakeBatch, new_value: float, user: User
) -> PeakAuditLog | None:
    """写入峰值并落审计。

    调用方必须已在当前事务内对 batch（及其 pond）行加锁，
    以保证两人并发改写同一峰值时只有一笔生效。

    值未变化时不改写、不记审计，返回 None。
    """
    old_value = batch.peak_temp_c
    assert_can_write_peak(batch.pond, old_value, user)
    if old_value is not None and abs(float(new_value) - float(old_value)) < 1e-9:
        return None
    batch.peak_temp_c = new_value
    log = PeakAuditLog(
        batch=batch,
        changed_by=user,
        old_value_c=old_value,
        new_value_c=new_value,
    )
    db.session.add(log)
    return log


def lock_pond_and_latest_batch(pond_id: int) -> tuple[Pond, SlakeBatch | None]:
    """对池及其最近批次加行级锁，须在事务内调用。"""
    pond = db.session.get(Pond, pond_id, with_for_update=True)
    if pond is None:
        db.session.rollback()
        from flask import abort

        abort(404)
    batch = latest_batch_for_pond(pond)
    if batch is not None:
        batch = db.session.get(SlakeBatch, batch.id, with_for_update=True)
    return pond, batch



def can_mark_pond_drawn(pond: Pond) -> tuple[bool, str]:
    """
    熟化池转为「已出灰」(drawn) 的前提：
    最近一条熟化批次的峰值温度已记录，且 >= 60℃。
    """
    latest = latest_batch_for_pond(pond)
    if latest is None:
        return False, "该池尚无熟化批次，不能标记为已出灰"
    if latest.peak_temp_c is None:
        return False, "最近批次尚未记录峰值温度，不能标记为已出灰"
    if latest.peak_temp_c < MIN_PEAK_TEMP_FOR_DRAWN:
        return (
            False,
            f"最近批次峰值温度 {latest.peak_temp_c}℃ 低于 {MIN_PEAK_TEMP_FOR_DRAWN:.0f}℃，不能标记为已出灰",
        )
    return True, ""


def assert_can_set_pond_status(pond: Pond, new_status: str) -> None:
    if new_status not in Pond.STATUS_CHOICES:
        raise RuleError(f"无效状态：{new_status}")
    if pond.status == Pond.STATUS_DRAWN and new_status != Pond.STATUS_DRAWN:
        raise RuleError("该池已出灰，状态已锁定，不能回退")
    if new_status == Pond.STATUS_DRAWN:
        ok, msg = can_mark_pond_drawn(pond)
        if not ok:
            raise RuleError(msg)
