"""峰值温度写入规则：首填 / 改写分界、并发互斥、审计落账。

所有能改 ``SlakeBatch.peak_temp_c`` 的入口（平面图抽屉、批次编辑页）
都必须走 :func:`apply_peak_write`，不得在视图里直接赋值。
"""

from __future__ import annotations

from flask_login import UserMixin
from sqlalchemy import select, update

from app.extensions import db
from app.models import PeakAudit, Pond, SlakeBatch


class PeakWriteError(ValueError):
    """峰值写入被业务规则拒绝（已出灰 / 越权改写 / 版本冲突 / 格式错）。"""


def parse_peak(raw: str) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        raise PeakWriteError("峰值温度格式无效")


def apply_peak_write(
    *,
    pond_id: int,
    batch_id: int,
    user: UserMixin,
    raw_value: str,
    expected_version: int | None,
) -> PeakAudit:
    """原子地完成一次峰值写入，成功才追加审计。

    规则：
      * 只有「熟化中」(slaking) 的池可写；已出灰后全员只读。
      * 峰值仍空 → 首填，操作工与管理员均可。
      * 峰值已有 → 改写，仅管理员；操作工只能看到只读数字。
      * 改写必须带打开表单时的 ``peak_version``。

    并发安全：最终落库用一条带条件的 UPDATE（改写要求
    ``peak_version == 期望值``，首填要求 ``peak_temp_c IS NULL``）。
    两人几乎同时提交同一笔已有峰值时，数据库行锁把两个 UPDATE 串行化，
    后到者的条件对不上新行 → 影响 0 行 → 拒绝，因此至多一笔生效，
    工人侧永远被拒，管理员侧至多一笔。

    成功时追加一行 :class:`PeakAudit`（旧值/新值/操作人/时间），
    返回该审计行；不自行 commit，由调用方与其余改动一起提交。
    失败抛 :class:`PeakWriteError`，调用方需 rollback。
    """
    new_value = parse_peak(raw_value)

    # PostgreSQL 下锁住池行，与状态变更（出灰）互斥；SQLite 忽略此子句。
    pond = db.session.scalar(select(Pond).where(Pond.id == pond_id).with_for_update())
    batch = db.session.scalar(
        select(SlakeBatch).where(
            SlakeBatch.id == batch_id, SlakeBatch.pond_id == pond_id
        )
    )
    if pond is None or batch is None:
        raise PeakWriteError("批次或熟化池不存在")

    if pond.status != Pond.STATUS_SLAKING:
        raise PeakWriteError("该池已不在熟化中，峰值温度全员只读")

    current = batch.peak_temp_c
    version = batch.peak_version or 0

    if current is not None:
        if not getattr(user, "is_admin", False):
            raise PeakWriteError(
                "该批次峰值已有记录，操作工只能首次登记；修正须由管理员进行"
            )
        if expected_version is None:
            raise PeakWriteError("缺少峰值版本号，请刷新页面后重试")
        action = PeakAudit.ACTION_EDIT
        old_value = current
        stmt = (
            update(SlakeBatch)
            .where(
                SlakeBatch.id == batch_id,
                SlakeBatch.peak_temp_c.isnot(None),
                SlakeBatch.peak_version == int(expected_version),
            )
            .values(peak_temp_c=new_value, peak_version=SlakeBatch.peak_version + 1)
        )
    else:
        action = PeakAudit.ACTION_FIRST
        old_value = None
        # 首填也带版本：页面打开后若已被他人抢先登记，本条件匹配 0 行。
        if expected_version is not None and int(expected_version) != version:
            raise PeakWriteError("峰值刚被他人登记，页面数据已过期，请刷新后重试")
        stmt = (
            update(SlakeBatch)
            .where(
                SlakeBatch.id == batch_id,
                SlakeBatch.peak_temp_c.is_(None),
                SlakeBatch.peak_version == version,
            )
            .values(peak_temp_c=new_value, peak_version=SlakeBatch.peak_version + 1)
        )

    # fetch: 让会话内 ORM 对象与条件更新后的行保持一致，不再手工赋值，
    # 避免 flush 时再发一条绕过版本条件的 UPDATE。
    result = db.session.execute(
        stmt.execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        # 并发下被他人抢先（版本已变 / 空峰值已被首填）。
        db.session.rollback()
        raise PeakWriteError("峰值刚被他人更新，页面数据已过期，请刷新后重试")

    audit = PeakAudit(
        batch_id=batch_id,
        pond_id=pond_id,
        actor_id=getattr(user, "id", None),
        actor_name=getattr(user, "username", "") or "",
        action=action,
        old_value=old_value,
        new_value=new_value,
    )
    db.session.add(audit)
    db.session.flush()
    return audit
